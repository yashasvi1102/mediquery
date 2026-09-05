"""
Role-based data filtering for MediQuery.

This is the core RBAC logic. It transforms query results BEFORE they
reach the frontend, based on the user's role. The key point for
interviews: Streamlit never sees data the role can't access.

| Field             | Doctor | Researcher | Admin | Patient     |
|-------------------|--------|------------|-------|-------------|
| patient_id        | raw    | hashed     | none  | own only    |
| given_name        | yes    | stripped   | none  | own only    |
| family_name       | yes    | stripped   | none  | own only    |
| clinical data     | yes    | yes        | none  | own only    |
| aggregate counts  | yes    | yes        | yes   | no          |
| provider metrics  | yes    | yes        | yes   | no          |
"""

import hashlib


def _hash_id(raw_id: str) -> str:
    """One-way hash of a patient ID for de-identification."""
    return "P-" + hashlib.sha256(raw_id.encode()).hexdigest()[:12]


def filter_for_role(results: list[dict], role: str,
                    patient_id: str | None = None) -> list[dict]:
    """
    Filter query results based on role.

    - doctor: no filtering
    - researcher: hash patient_ids, strip names
    - admin: remove all patient-level fields, keep aggregates
    - patient: filter to own patient_id only
    """
    if not results:
        return results

    if role == "doctor":
        return results

    if role == "researcher":
        return _filter_researcher(results)

    if role == "admin":
        return _filter_admin(results)

    if role == "patient":
        return _filter_patient(results, patient_id)

    return results


def _filter_researcher(results: list[dict]) -> list[dict]:
    """De-identify: hash patient IDs, strip names."""
    filtered = []
    for row in results:
        new_row = {}
        for key, val in row.items():
            clean_key = key.split(".")[-1] if "." in key else key

            if clean_key == "patient_id" and val:
                new_row[key] = _hash_id(str(val))
            elif clean_key in ("given_name", "family_name"):
                continue  # strip names entirely
            else:
                new_row[key] = val
        filtered.append(new_row)
    return filtered


def _filter_admin(results: list[dict]) -> list[dict]:
    """Keep only aggregate/operational fields, no patient-level data."""
    patient_fields = {
        "patient_id", "given_name", "family_name", "birth_date",
        "deceased_date", "marital_status", "race", "ethnicity",
        "city", "state", "postal_code", "country",
        "encounter_id", "condition_id", "medication_request_id",
    }

    filtered = []
    for row in results:
        new_row = {}
        for key, val in row.items():
            clean_key = key.split(".")[-1] if "." in key else key
            if clean_key not in patient_fields:
                new_row[key] = val
        filtered.append(new_row)

    # If all fields were stripped, return aggregate summary
    if filtered and all(len(r) == 0 for r in filtered):
        return [{"note": "Patient-level data not available for admin role.",
                 "row_count": len(results)}]

    return filtered


def _filter_patient(results: list[dict], patient_id: str | None) -> list[dict]:
    """Return only rows matching the patient's own ID."""
    if not patient_id:
        return [{"error": "No patient_id associated with this account."}]

    filtered = []
    for row in results:
        # Check if any field in this row matches the patient's ID
        match = False
        for key, val in row.items():
            clean_key = key.split(".")[-1] if "." in key else key
            if clean_key == "patient_id" and str(val) == patient_id:
                match = True
                break
        if match:
            filtered.append(row)

    if not filtered:
        return [{"note": "No records found for your patient ID in this query."}]

    return filtered


def filter_answer_for_role(answer: str, role: str) -> str:
    """Filter the text answer based on role."""
    if role == "doctor":
        return answer

    if role == "researcher":
        return answer  # citations already use hashed IDs from filtered results

    if role == "admin":
        return "Aggregate data only. Patient-level details are restricted for admin users."

    if role == "patient":
        return answer  # filtered to own data already

    return answer