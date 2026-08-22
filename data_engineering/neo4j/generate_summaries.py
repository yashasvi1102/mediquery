"""
Day 26: Generate patient summaries from structured Silver data.

Each patient gets a text summary built from demographics, conditions,
medications, and encounter history. These feed into Chroma (Day 27)
for semantic search.

Approach: template-based (DD-007). Deterministic, free, reproducible.
No LLM calls — structured data → formatted text.

Usage (from project root):
    python data_engineering/neo4j/generate_summaries.py

Output: data_generation/parsed/patient_summaries.parquet
"""

import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
DUCKDB_PATH = _PROJECT_ROOT / "mediquery.duckdb"
OUTPUT_PATH = _PROJECT_ROOT / "data_generation" / "parsed" / "patient_summaries.parquet"


def get_duckdb_conn():
    if not DUCKDB_PATH.exists():
        print(f"ERROR: DuckDB not found at {DUCKDB_PATH}")
        sys.exit(1)
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def load_patient_demographics(conn):
    """Base patient info."""
    print("  Loading demographics...")
    return conn.execute("""
        SELECT
            patient_id, given_name, family_name, gender, birth_date,
            is_deceased, deceased_date, age_years_current, age_at_death,
            age_group, marital_status, race, ethnicity, city, state
        FROM silver.silver_patients
    """).fetchdf()


def load_condition_summaries(conn):
    """Aggregated conditions per patient."""
    print("  Loading conditions...")
    return conn.execute("""
        SELECT
            patient_id,
            COUNT(DISTINCT snomed_code) AS unique_condition_count,
            COUNT(*) AS total_condition_episodes,
            STRING_AGG(DISTINCT
                CASE WHEN clinical_category = 'disorder' THEN display END,
                ', '
            ) AS disorder_list,
            STRING_AGG(DISTINCT
                CASE WHEN condition_flag IS NOT NULL THEN condition_flag END,
                ', '
            ) AS chronic_flags,
            SUM(CASE WHEN clinical_category = 'disorder' THEN 1 ELSE 0 END) AS disorder_count,
            SUM(CASE WHEN clinical_category = 'finding' THEN 1 ELSE 0 END) AS finding_count,
            SUM(CASE WHEN is_billable_diagnosis THEN 1 ELSE 0 END) AS billable_count
        FROM silver.silver_conditions
        GROUP BY patient_id
    """).fetchdf()


def load_medication_summaries(conn):
    """Aggregated medications per patient."""
    print("  Loading medications...")
    return conn.execute("""
        SELECT
            patient_id,
            COUNT(DISTINCT rxnorm_code) AS unique_medication_count,
            COUNT(*) AS total_prescriptions,
            STRING_AGG(DISTINCT medication_display, ', ') AS medication_list,
            STRING_AGG(DISTINCT
                CASE WHEN medication_flag IS NOT NULL THEN medication_flag END,
                ', '
            ) AS chronic_med_flags,
            STRING_AGG(DISTINCT drug_class, ', ') AS drug_classes
        FROM silver.silver_medications
        GROUP BY patient_id
    """).fetchdf()


def load_encounter_summaries(conn):
    """Aggregated encounter stats per patient."""
    print("  Loading encounters...")
    return conn.execute("""
        SELECT
            patient_id,
            COUNT(*) AS total_encounters,
            SUM(CASE WHEN encounter_type = 'ambulatory' THEN 1 ELSE 0 END) AS ambulatory_count,
            SUM(CASE WHEN encounter_type = 'emergency' THEN 1 ELSE 0 END) AS emergency_count,
            SUM(CASE WHEN encounter_type = 'inpatient' THEN 1 ELSE 0 END) AS inpatient_count,
            SUM(CASE WHEN encounter_type = 'virtual' THEN 1 ELSE 0 END) AS virtual_count,
            MIN(start_time) AS first_encounter,
            MAX(start_time) AS last_encounter,
            ROUND(
                (EXTRACT(EPOCH FROM MAX(start_time)) - EXTRACT(EPOCH FROM MIN(start_time)))
                / (365.25 * 86400), 1
            ) AS care_span_years
        FROM silver.silver_encounters
        GROUP BY patient_id
    """).fetchdf()


def truncate_list(text, max_items=10):
    """Truncate a comma-separated list to max_items, adding '...' if needed."""
    if text is None or pd.isna(text):
        return None
    items = [x.strip() for x in text.split(",")]
    if len(items) <= max_items:
        return ", ".join(items)
    return ", ".join(items[:max_items]) + f", and {len(items) - max_items} more"


def build_summary(row):
    """Build a text summary for one patient from the merged row."""
    parts = []

    # Demographics
    age = row.get("age_at_death") if pd.notna(row.get("age_at_death")) else row.get("age_years_current")
    age_str = f"{int(age)}-year-old" if pd.notna(age) else "Age unknown"
    gender = row.get("gender", "unknown")
    race = row.get("race")
    ethnicity = row.get("ethnicity")

    demo = f"{age_str} {gender}"
    if pd.notna(race) and race.lower() not in ("unknown", "other"):
        demo += f", {race}"
    if pd.notna(ethnicity) and ethnicity.lower() != "nonhispanic":
        demo += f", {ethnicity}"
    demo += f" from {row.get('city', 'unknown')}, {row.get('state', 'unknown')}."
    if row.get("is_deceased"):
        demo += " Deceased."
    parts.append(demo)

    # Marital status
    ms = row.get("marital_status")
    ms_map = {"M": "Married", "S": "Single", "D": "Divorced", "W": "Widowed"}
    if pd.notna(ms) and ms in ms_map:
        parts.append(f"{ms_map[ms]}.")

    # Encounter history
    total_enc = row.get("total_encounters", 0)
    if pd.notna(total_enc) and total_enc > 0:
        care_span = row.get("care_span_years", 0)
        enc_parts = [f"{int(total_enc)} encounters"]
        if pd.notna(care_span) and care_span > 0:
            enc_parts.append(f"over {care_span} years")

        type_details = []
        for etype, col in [("ambulatory", "ambulatory_count"),
                           ("emergency", "emergency_count"),
                           ("inpatient", "inpatient_count"),
                           ("virtual", "virtual_count")]:
            n = row.get(col, 0)
            if pd.notna(n) and n > 0:
                type_details.append(f"{int(n)} {etype}")

        if type_details:
            enc_parts.append(f"({', '.join(type_details)})")

        parts.append(" ".join(enc_parts) + ".")

    # Conditions
    chronic = row.get("chronic_flags")
    disorders = row.get("disorder_list")
    disorder_count = row.get("disorder_count", 0)

    if pd.notna(chronic):
        flags = chronic.replace("_", " ").replace("t2", "type 2")
        parts.append(f"Chronic conditions: {flags}.")

    if pd.notna(disorders):
        truncated = truncate_list(disorders, max_items=8)
        parts.append(f"Diagnoses ({int(disorder_count)} episodes): {truncated}.")

    billable = row.get("billable_count", 0)
    if pd.notna(billable) and billable > 0:
        parts.append(f"{int(billable)} billable diagnoses.")

    # Medications
    med_list = row.get("medication_list")
    med_count = row.get("unique_medication_count", 0)
    chronic_meds = row.get("chronic_med_flags")
    drug_cls = row.get("drug_classes")

    if pd.notna(med_list):
        truncated = truncate_list(med_list, max_items=8)
        parts.append(f"Medications ({int(med_count)} unique): {truncated}.")

    if pd.notna(chronic_meds):
        flags = chronic_meds.replace("_", " ")
        parts.append(f"Chronic medication categories: {flags}.")

    if pd.notna(drug_cls):
        truncated = truncate_list(drug_cls, max_items=6)
        parts.append(f"Drug classes: {truncated}.")

    return " ".join(parts)


def main():
    print("Day 26: Generating patient summaries")
    print(f"DuckDB: {DUCKDB_PATH}")
    start = time.time()

    conn = get_duckdb_conn()

    # Load all aggregates
    demographics = load_patient_demographics(conn)
    conditions = load_condition_summaries(conn)
    medications = load_medication_summaries(conn)
    encounters = load_encounter_summaries(conn)

    print(f"\n  Loaded in {time.time() - start:.1f}s")
    print(f"  Demographics: {len(demographics):,}")
    print(f"  Condition aggregates: {len(conditions):,}")
    print(f"  Medication aggregates: {len(medications):,}")
    print(f"  Encounter aggregates: {len(encounters):,}")

    # Merge all on patient_id
    print("\n  Merging...")
    merged = demographics.merge(conditions, on="patient_id", how="left")
    merged = merged.merge(medications, on="patient_id", how="left")
    merged = merged.merge(encounters, on="patient_id", how="left")

    assert len(merged) == len(demographics), (
        f"Merge changed row count: {len(demographics)} -> {len(merged)}"
    )

    # Generate summaries
    print("  Generating summaries...")
    gen_start = time.time()
    merged["summary_text"] = merged.apply(build_summary, axis=1)
    print(f"  Generated {len(merged):,} summaries in {time.time() - gen_start:.1f}s")

    # Summary stats
    lengths = merged["summary_text"].str.len()
    print(f"\n  Summary length stats:")
    print(f"    Min:    {lengths.min()} chars")
    print(f"    Median: {int(lengths.median())} chars")
    print(f"    Max:    {lengths.max()} chars")
    print(f"    Mean:   {int(lengths.mean())} chars")

    # Preview
    print("\n  === Sample summaries ===")
    for i in [0, 100, 5000]:
        if i < len(merged):
            pid = merged.iloc[i]["patient_id"][:12]
            print(f"\n  [{pid}...]")
            print(f"  {merged.iloc[i]['summary_text'][:300]}...")

    # Save
    output_df = merged[["patient_id", "summary_text"]].copy()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_parquet(str(OUTPUT_PATH), index=False)
    print(f"\n  Saved to {OUTPUT_PATH}")
    print(f"  File size: {OUTPUT_PATH.stat().st_size / 1024 / 1024:.1f} MB")

    conn.close()
    elapsed = time.time() - start
    print(f"\nDay 26 complete in {elapsed:.1f}s. {len(output_df):,} summaries ready for Chroma.")


if __name__ == "__main__":
    main()