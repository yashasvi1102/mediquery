"""
FHIR Parser for Synthea-generated patient bundles.

Design decisions:
- Raw JSON parsing (not fhir.resources lib) for ~10-15x speed at 1000+ bundles.
- Flat dicts as output, ready for pandas / Snowflake batch insert.
- References normalized: Synthea writes "urn:uuid:abc-123" inside bundles
  and "Patient/abc-123" across bundles. Both reduce to the bare UUID here
  so foreign key joins work later in the Silver layer.
- Missing fields return None rather than KeyError; clinical data is messy
  even in synthetic form.

Usage:
    python fhir_parser.py path/to/synthea_bundle.json

Or as a library:
    from fhir_parser import load_bundle, parse_bundle
    bundle = load_bundle("output/fhir/John_Doe_abc123.json")
    parsed = parse_bundle(bundle)
    # parsed["patients"], parsed["encounters"], etc.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


# ---------- bundle I/O ----------

def load_bundle(path: str | Path) -> dict:
    """Load a single FHIR bundle JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_resources(bundle: dict, resource_type: str) -> Iterator[dict]:
    """Yield every resource of a given type from a bundle's entry list."""
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == resource_type:
            yield resource


# ---------- helpers ----------

def _safe_get(d: Any, *keys, default=None):
    """
    Walk a nested dict/list path; return default if any step is missing.
    Use ints for list indices: _safe_get(p, "name", 0, "family").
    """
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int):
            cur = cur[k] if 0 <= k < len(cur) else None
        else:
            return default
        if cur is None:
            return default
    return cur


def _normalize_ref(ref: str | None) -> str | None:
    """
    Normalize FHIR refs to bare IDs.
        'urn:uuid:abc-123'                                    -> 'abc-123'
        'Patient/abc-123'                                     -> 'abc-123'
        'Organization?identifier=https://.../synthea|abc-123' -> 'abc-123'
        'abc-123'                                             -> 'abc-123'
    """
    if not ref:
        return None
    if ref.startswith("urn:uuid:"):
        return ref[len("urn:uuid:"):]
    # Conditional reference (Synthea uses this for cross-bundle Organization/Practitioner).
    # Identifier value comes after the last pipe.
    if "?identifier=" in ref:
        return ref.rsplit("|", 1)[-1] if "|" in ref else ref.rsplit("=", 1)[-1]
    # Standard literal reference: ResourceType/id
    if "/" in ref:
        return ref.split("/", 1)[1]
    return ref


def _us_core_extension(p: dict, url_suffix: str) -> str | None:
    """
    Pull a US Core extension value (race, ethnicity) by URL suffix.
    Synthea uses the standard US Core extension URLs.
    """
    for ext in p.get("extension", []) or []:
        if ext.get("url", "").endswith(url_suffix):
            for sub in ext.get("extension", []) or []:
                if sub.get("url") == "ombCategory":
                    return _safe_get(sub, "valueCoding", "display")
    return None


# ---------- per-resource extractors ----------

def extract_patient(p: dict) -> dict:
    """Flatten a Patient resource."""
    name = _safe_get(p, "name", 0) or {}
    address = _safe_get(p, "address", 0) or {}
    return {
        "patient_id": p.get("id"),
        "given_name": " ".join(name.get("given", []) or []) or None,
        "family_name": name.get("family"),
        "gender": p.get("gender"),
        "birth_date": p.get("birthDate"),
        "deceased_date": p.get("deceasedDateTime"),
        "marital_status": _safe_get(p, "maritalStatus", "coding", 0, "code"),
        "race": _us_core_extension(p, "us-core-race"),
        "ethnicity": _us_core_extension(p, "us-core-ethnicity"),
        "city": address.get("city"),
        "state": address.get("state"),
        "postal_code": address.get("postalCode"),
        "country": address.get("country"),
    }


def extract_encounter(e: dict) -> dict:
    """Flatten an Encounter resource."""
    period = e.get("period") or {}
    type_coding = _safe_get(e, "type", 0, "coding", 0) or {}
    reason_coding = _safe_get(e, "reasonCode", 0, "coding", 0) or {}
    return {
        "encounter_id": e.get("id"),
        "patient_id": _normalize_ref(_safe_get(e, "subject", "reference")),
        "status": e.get("status"),
        # 'class' is a reserved word in some contexts; FHIR uses it for
        # encounter class (inpatient, ambulatory, emergency...).
        "class_code": _safe_get(e, "class", "code"),
        "class_display": _safe_get(e, "class", "display"),
        "type_code": type_coding.get("code"),
        "type_display": type_coding.get("display"),
        "reason_code": reason_coding.get("code"),
        "reason_display": reason_coding.get("display"),
        "start_time": period.get("start"),
        "end_time": period.get("end"),
        "provider_id": _normalize_ref(_safe_get(e, "serviceProvider", "reference")),
    }


def extract_condition(c: dict) -> dict:
    """Flatten a Condition resource. Code system is usually SNOMED for Synthea."""
    code = _safe_get(c, "code", "coding", 0) or {}
    return {
        "condition_id": c.get("id"),
        "patient_id": _normalize_ref(_safe_get(c, "subject", "reference")),
        "encounter_id": _normalize_ref(_safe_get(c, "encounter", "reference")),
        "code_system": code.get("system"),
        "code": code.get("code"),
        "display": code.get("display"),
        "clinical_status": _safe_get(c, "clinicalStatus", "coding", 0, "code"),
        "verification_status": _safe_get(c, "verificationStatus", "coding", 0, "code"),
        "onset_date": c.get("onsetDateTime"),
        "abatement_date": c.get("abatementDateTime"),
        "recorded_date": c.get("recordedDate"),
    }
def build_medication_lookup(bundle: dict) -> dict[str, dict]:
    """
    Scan bundle for Medication resources and return id -> coding dict.

    Synthea writes some MedicationRequests with medicationReference instead
    of medicationCodeableConcept; the referenced Medication resource lives
    in the same bundle. Without this lookup, ~35% of medications parse as NULL.
    Keyed on bare UUID (matching _normalize_ref output) so the MedicationRequest
    extractor can look up the resolved reference directly.
    """
    lookup: dict[str, dict] = {}
    for med in iter_resources(bundle, "Medication"):
        coding = _safe_get(med, "code", "coding", 0) or {}
        med_id = med.get("id")
        if med_id:
            lookup[med_id] = {
                "system": coding.get("system"),
                "code": coding.get("code"),
                "display": coding.get("display"),
            }
    return lookup

def extract_medication_request(m: dict, med_lookup: dict[str, dict] | None = None) -> dict:
    """
    Flatten a MedicationRequest resource (RxNorm-coded in Synthea).

    Synthea uses two forms:
      1. medicationCodeableConcept - inline drug code (preferred path)
      2. medicationReference        - points to a Medication resource in the
                                      same bundle; resolved via med_lookup.
    """
    # Primary: inline coding
    med_coding = _safe_get(m, "medicationCodeableConcept", "coding", 0) or {}

    # Fallback: reference to a Medication resource elsewhere in the bundle
    if not med_coding.get("code") and med_lookup:
        ref_id = _normalize_ref(_safe_get(m, "medicationReference", "reference"))
        if ref_id and ref_id in med_lookup:
            med_coding = med_lookup[ref_id]

    dosage = _safe_get(m, "dosageInstruction", 0) or {}
    return {
        "medication_request_id": m.get("id"),
        "patient_id": _normalize_ref(_safe_get(m, "subject", "reference")),
        "encounter_id": _normalize_ref(_safe_get(m, "encounter", "reference")),
        "status": m.get("status"),
        "intent": m.get("intent"),
        "code_system": med_coding.get("system"),
        "medication_code": med_coding.get("code"),
        "medication_display": med_coding.get("display"),
        "authored_on": m.get("authoredOn"),
        "dosage_text": dosage.get("text"),
    }
def extract_observations(o: dict) -> list[dict]:
    """
    Flatten an Observation resource. Returns a LIST because blood-pressure-style
    observations (component arrays) are split into one row per component.

    Synthea emits four shapes:
      - valueQuantity        -> numeric lab/vital (HbA1c, glucose, weight)
      - component[]          -> multi-part (blood pressure: systolic + diastolic)
      - valueCodeableConcept -> categorical (smoking status, SNOMED-coded)
      - valueString          -> free-text narrative (procedure notes)

    Storage strategy (Option 3): blood pressure splits into 2 rows, each with its
    own LOINC code. Every row has the same column shape regardless of source.
    Querying BP becomes: WHERE loinc_code = '8480-6' (systolic).
    """
    base_id = o.get("id")
    patient_id = _normalize_ref(_safe_get(o, "subject", "reference"))
    encounter_id = _normalize_ref(_safe_get(o, "encounter", "reference"))
    effective = o.get("effectiveDateTime")
    issued = o.get("issued")
    status = o.get("status")
    category = _safe_get(o, "category", 0, "coding", 0, "code")
    parent_code = _safe_get(o, "code", "coding", 0) or {}

    rows: list[dict] = []

    # CASE 1: component array (blood pressure has valueQuantity components;
    # PRAPARE social-determinants survey has valueCodeableConcept components).
    # Emit one row per component, handling each value shape.
    components = o.get("component") or []
    if components:
        for idx, comp in enumerate(components):
            coding = _safe_get(comp, "code", "coding", 0) or {}
            comp_vq = comp.get("valueQuantity")
            comp_vcc = comp.get("valueCodeableConcept")
            comp_vs = comp.get("valueString")

            value_numeric = None
            value_text = None
            value_code = None
            value_code_system = None
            unit = None

            if comp_vq is not None:
                value_numeric = comp_vq.get("value")
                unit = comp_vq.get("unit")
            elif comp_vcc is not None:
                inner = _safe_get(comp_vcc, "coding", 0) or {}
                value_text = comp_vcc.get("text") or inner.get("display")
                value_code = inner.get("code")
                value_code_system = inner.get("system")
            elif comp_vs is not None:
                value_text = comp_vs

            rows.append({
                "observation_id": f"{base_id}_{idx}",
                "parent_observation_id": base_id,
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "status": status,
                "category": category,
                "loinc_code": coding.get("code"),
                "loinc_display": coding.get("display"),
                "value_numeric": value_numeric,
                "value_text": value_text,
                "value_code": value_code,
                "value_code_system": value_code_system,
                "unit": unit,
                "effective_date": effective,
                "issued_date": issued,
            })
        return rows

    # CASE 2: valueQuantity (numeric lab or vital)
    vq = o.get("valueQuantity")
    if vq is not None:
        rows.append({
            "observation_id": base_id,
            "parent_observation_id": None,
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "status": status,
            "category": category,
            "loinc_code": parent_code.get("code"),
            "loinc_display": parent_code.get("display"),
            "value_numeric": vq.get("value"),
            "value_text": None,
            "value_code": None,
            "value_code_system": None,
            "unit": vq.get("unit"),
            "effective_date": effective,
            "issued_date": issued,
        })
        return rows

    # CASE 3: valueCodeableConcept (categorical: smoking status, etc.)
    vcc = o.get("valueCodeableConcept")
    if vcc is not None:
        coded = _safe_get(vcc, "coding", 0) or {}
        rows.append({
            "observation_id": base_id,
            "parent_observation_id": None,
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "status": status,
            "category": category,
            "loinc_code": parent_code.get("code"),
            "loinc_display": parent_code.get("display"),
            "value_numeric": None,
            "value_text": vcc.get("text") or coded.get("display"),
            "value_code": coded.get("code"),
            "value_code_system": coded.get("system"),
            "unit": None,
            "effective_date": effective,
            "issued_date": issued,
        })
        return rows

    # CASE 4: valueString (free-text narrative)
    vs = o.get("valueString")
    if vs is not None:
        rows.append({
            "observation_id": base_id,
            "parent_observation_id": None,
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "status": status,
            "category": category,
            "loinc_code": parent_code.get("code"),
            "loinc_display": parent_code.get("display"),
            "value_numeric": None,
            "value_text": vs,
            "value_code": None,
            "value_code_system": None,
            "unit": None,
            "effective_date": effective,
            "issued_date": issued,
        })
        return rows

    # CASE 5: no value at all (skip; rare edge case)
    return rows
# ---------- bundle-level convenience ----------

RESOURCE_EXTRACTORS = {
    "Patient": ("patients", extract_patient),
    "Encounter": ("encounters", extract_encounter),
    "Condition": ("conditions", extract_condition),
    "MedicationRequest": ("medication_requests", extract_medication_request),
    "Observation": ("observations", extract_observations),
}


def parse_bundle(bundle: dict) -> dict[str, list[dict]]:
    """Parse one Synthea bundle into typed lists of flat dicts."""
    out: dict[str, list[dict]] = {key: [] for key, _ in RESOURCE_EXTRACTORS.values()}

    # MedicationRequest needs a Medication-resource lookup for reference fallback.
    med_lookup = build_medication_lookup(bundle)

    for resource_type, (key, extractor) in RESOURCE_EXTRACTORS.items():
        for resource in iter_resources(bundle, resource_type):
            if resource_type == "MedicationRequest":
                out[key].append(extractor(resource, med_lookup))
            elif resource_type == "Observation":
                # Observation extractor returns a list (BP -> 2 rows)
                out[key].extend(extractor(resource))
            else:
                out[key].append(extractor(resource))
    return out


# ---------- smoke test ----------

if __name__ == "__main__":
    import sys
    from pprint import pprint

    if len(sys.argv) < 2:
        print("Usage: python fhir_parser.py <path_to_synthea_bundle.json>")
        print("Example: python fhir_parser.py ../synthea/output/fhir/John_Doe_abc.json")
        sys.exit(1)

    bundle_path = sys.argv[1]
    bundle = load_bundle(bundle_path)
    parsed = parse_bundle(bundle)

    print(f"Parsed bundle: {bundle_path}\n")
    for resource_key, records in parsed.items():
        print(f"=== {resource_key} ({len(records)} records) ===")
        if records:
            pprint(records[0])
            print()
            