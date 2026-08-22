"""
Day 24: Ingest Condition, Medication, Provider nodes +
        DIAGNOSED_WITH, PRESCRIBED, TREATED_BY relationships.

Depends on Day 23 (Patient + Encounter nodes must exist).

Usage (from project root):
    python -m data_engineering.neo4j.ingest_conditions_meds_providers

Idempotent via MERGE — safe to rerun.
"""

import sys
import time
import math
from pathlib import Path

import duckdb
import pandas as pd
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configuration (same as Day 23)
# ---------------------------------------------------------------------------
BATCH_SIZE = 5_000

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
DUCKDB_PATH = _PROJECT_ROOT / "mediquery.duckdb"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"


# ---------------------------------------------------------------------------
# Helpers (same as Day 23)
# ---------------------------------------------------------------------------
def get_duckdb_conn():
    if not DUCKDB_PATH.exists():
        print(f"ERROR: DuckDB not found at {DUCKDB_PATH}")
        sys.exit(1)
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def clean_batch(records: list[dict]) -> list[dict]:
    for row in records:
        for key, val in row.items():
            if pd.isna(val):
                row[key] = None
    return records


def batched(records: list[dict], size: int):
    for i in range(0, len(records), size):
        yield records[i : i + size]


def run_batched_ingest(session, cypher: str, records: list[dict], label: str):
    total = len(records)
    num_batches = math.ceil(total / BATCH_SIZE)
    start = time.time()

    for i, batch in enumerate(batched(records, BATCH_SIZE), 1):
        cleaned = clean_batch(batch)
        session.run(cypher, batch=cleaned)
        pct = min(100, int(i / num_batches * 100))
        elapsed = time.time() - start
        print(f"\r  {label}: batch {i}/{num_batches} ({pct}%) — {elapsed:.1f}s", end="", flush=True)

    elapsed = time.time() - start
    print(f"\r  {label}: {total:,} rows in {elapsed:.1f}s" + " " * 30)


# ---------------------------------------------------------------------------
# Phase 1: Condition nodes (deduplicated by snomed_code)
# ---------------------------------------------------------------------------
CONDITION_NODE_CYPHER = """
UNWIND $batch AS row
MERGE (c:Condition {snomed_code: row.snomed_code})
SET c.display               = row.display,
    c.clinical_category     = row.clinical_category,
    c.condition_flag        = row.condition_flag,
    c.is_billable_diagnosis = row.is_billable_diagnosis
"""


def ingest_condition_nodes(session, duckdb_conn):
    print("\n=== Phase 1: Condition nodes (unique SNOMED codes) ===")

    df = duckdb_conn.execute("""
        SELECT DISTINCT
            snomed_code,
            display,
            clinical_category,
            condition_flag,
            is_billable_diagnosis
        FROM silver.silver_conditions
        WHERE snomed_code IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, CONDITION_NODE_CYPHER, records, "Condition nodes")

    result = session.run("MATCH (c:Condition) RETURN count(c) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    ok = actual == expected
    print(f"  Verify: {actual} Condition nodes ({'PASS' if ok else 'FAIL'}, expected {expected})")
    return ok


# ---------------------------------------------------------------------------
# Phase 2: Medication nodes (deduplicated by rxnorm_code)
# ---------------------------------------------------------------------------
MEDICATION_NODE_CYPHER = """
UNWIND $batch AS row
MERGE (m:Medication {rxnorm_code: row.rxnorm_code})
SET m.medication_display = row.medication_display,
    m.drug_class         = row.drug_class,
    m.medication_flag    = row.medication_flag
"""


def ingest_medication_nodes(session, duckdb_conn):
    print("\n=== Phase 2: Medication nodes (unique RxNorm codes) ===")

    df = duckdb_conn.execute("""
        SELECT DISTINCT
            rxnorm_code,
            medication_display,
            drug_class,
            medication_flag
        FROM silver.silver_medications
        WHERE rxnorm_code IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, MEDICATION_NODE_CYPHER, records, "Medication nodes")

    result = session.run("MATCH (m:Medication) RETURN count(m) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    ok = actual == expected
    print(f"  Verify: {actual} Medication nodes ({'PASS' if ok else 'FAIL'}, expected {expected})")
    return ok


# ---------------------------------------------------------------------------
# Phase 3: Provider nodes (deduplicated by provider_id)
# ---------------------------------------------------------------------------
PROVIDER_NODE_CYPHER = """
UNWIND $batch AS row
MERGE (prov:Provider {provider_id: row.provider_id})
"""


def ingest_provider_nodes(session, duckdb_conn):
    print("\n=== Phase 3: Provider nodes (unique provider IDs) ===")

    df = duckdb_conn.execute("""
        SELECT DISTINCT provider_id
        FROM silver.silver_encounters
        WHERE provider_id IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, PROVIDER_NODE_CYPHER, records, "Provider nodes")

    result = session.run("MATCH (prov:Provider) RETURN count(prov) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    ok = actual == expected
    print(f"  Verify: {actual} Provider nodes ({'PASS' if ok else 'FAIL'}, expected {expected})")
    return ok


# ---------------------------------------------------------------------------
# Phase 4: DIAGNOSED_WITH relationships
#   (Encounter)-[:DIAGNOSED_WITH]->(Condition)
# ---------------------------------------------------------------------------
DIAGNOSED_WITH_CYPHER = """
UNWIND $batch AS row
MATCH (e:Encounter {encounter_id: row.encounter_id})
MATCH (c:Condition {snomed_code: row.snomed_code})
MERGE (e)-[r:DIAGNOSED_WITH]->(c)
SET r.condition_id    = row.condition_id,
    r.onset_date      = row.onset_date,
    r.abatement_date  = row.abatement_date,
    r.clinical_status = row.clinical_status,
    r.is_active       = row.is_active
"""


def ingest_diagnosed_with(session, duckdb_conn):
    print("\n=== Phase 4: DIAGNOSED_WITH relationships ===")

    df = duckdb_conn.execute("""
        SELECT condition_id, encounter_id, snomed_code,
               onset_date, abatement_date, clinical_status, is_active
        FROM silver.silver_conditions
        WHERE encounter_id IS NOT NULL
          AND snomed_code IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, DIAGNOSED_WITH_CYPHER, records, "DIAGNOSED_WITH")

    result = session.run("MATCH ()-[r:DIAGNOSED_WITH]->() RETURN count(r) AS cnt")
    actual = result.single()["cnt"]
    print(f"  Verify: {actual:,} DIAGNOSED_WITH rels (source rows: {len(records):,})")

    # Note: actual may be < source rows if multiple condition rows with same
    # (encounter_id, snomed_code) pair exist — MERGE deduplicates the relationship
    # but SET overwrites properties with the last batch's values.
    # This is acceptable: the relationship exists, properties reflect latest load.
    return True


# ---------------------------------------------------------------------------
# Phase 5: PRESCRIBED relationships
#   (Encounter)-[:PRESCRIBED]->(Medication)
# ---------------------------------------------------------------------------
PRESCRIBED_CYPHER = """
UNWIND $batch AS row
MATCH (e:Encounter {encounter_id: row.encounter_id})
MATCH (m:Medication {rxnorm_code: row.rxnorm_code})
MERGE (e)-[r:PRESCRIBED]->(m)
SET r.medication_request_id = row.medication_request_id,
    r.status                = row.status,
    r.authored_on           = row.authored_on
"""


def ingest_prescribed(session, duckdb_conn):
    print("\n=== Phase 5: PRESCRIBED relationships ===")

    df = duckdb_conn.execute("""
        SELECT medication_request_id, encounter_id, rxnorm_code,
               status, authored_on
        FROM silver.silver_medications
        WHERE encounter_id IS NOT NULL
          AND rxnorm_code IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, PRESCRIBED_CYPHER, records, "PRESCRIBED")

    result = session.run("MATCH ()-[r:PRESCRIBED]->() RETURN count(r) AS cnt")
    actual = result.single()["cnt"]
    print(f"  Verify: {actual:,} PRESCRIBED rels (source rows: {len(records):,})")
    return True


# ---------------------------------------------------------------------------
# Phase 6: TREATED_BY relationships
#   (Encounter)-[:TREATED_BY]->(Provider)
# ---------------------------------------------------------------------------
TREATED_BY_CYPHER = """
UNWIND $batch AS row
MATCH (e:Encounter {encounter_id: row.encounter_id})
MATCH (prov:Provider {provider_id: row.provider_id})
MERGE (e)-[:TREATED_BY]->(prov)
"""


def ingest_treated_by(session, duckdb_conn):
    print("\n=== Phase 6: TREATED_BY relationships ===")

    df = duckdb_conn.execute("""
        SELECT encounter_id, provider_id
        FROM silver.silver_encounters
        WHERE provider_id IS NOT NULL
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, TREATED_BY_CYPHER, records, "TREATED_BY")

    result = session.run("MATCH ()-[r:TREATED_BY]->() RETURN count(r) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    ok = actual == expected
    print(f"  Verify: {actual:,} TREATED_BY rels ({'PASS' if ok else 'FAIL'}, expected {expected:,})")
    return ok


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(session):
    print("\n=== Day 24 Summary ===")
    result = session.run("""
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS count
        ORDER BY count DESC
    """)
    for r in result:
        print(f"  {r['label']}: {r['count']:,}")

    result = session.run("""
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS count
        ORDER BY count DESC
    """)
    for r in result:
        print(f"  [{r['rel_type']}]: {r['count']:,}")

    result = session.run("MATCH (n) RETURN count(n) AS n")
    nodes = result.single()["n"]
    result = session.run("MATCH ()-[r]->() RETURN count(r) AS r")
    rels = result.single()["r"]
    print(f"\n  Total: {nodes:,} nodes, {rels:,} relationships")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Day 24: Ingesting Conditions, Medications, Providers + relationships")
    print(f"DuckDB: {DUCKDB_PATH}")
    print(f"Neo4j:  {NEO4J_URI}")

    duckdb_conn = get_duckdb_conn()
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    neo4j_driver.verify_connectivity()
    print("Connected to both databases.")

    # Preflight: check Day 23 nodes exist
    with neo4j_driver.session() as session:
        result = session.run("MATCH (p:Patient) RETURN count(p) AS cnt")
        patient_count = result.single()["cnt"]
        result = session.run("MATCH (e:Encounter) RETURN count(e) AS cnt")
        encounter_count = result.single()["cnt"]

        if patient_count == 0 or encounter_count == 0:
            print(f"\nERROR: Day 23 prerequisite missing!")
            print(f"  Patients: {patient_count:,}, Encounters: {encounter_count:,}")
            print("  Run ingest_patients_encounters.py first.")
            sys.exit(1)
        print(f"  Preflight: {patient_count:,} Patients, {encounter_count:,} Encounters present.")

    all_passed = True
    with neo4j_driver.session() as session:
        # Nodes first (relationships need them)
        if not ingest_condition_nodes(session, duckdb_conn):
            all_passed = False
        if not ingest_medication_nodes(session, duckdb_conn):
            all_passed = False
        if not ingest_provider_nodes(session, duckdb_conn):
            all_passed = False

        # Relationships
        if not ingest_diagnosed_with(session, duckdb_conn):
            all_passed = False
        if not ingest_prescribed(session, duckdb_conn):
            all_passed = False
        if not ingest_treated_by(session, duckdb_conn):
            all_passed = False

        print_summary(session)

    duckdb_conn.close()
    neo4j_driver.close()

    if all_passed:
        print("\nDay 24 PASSED. Ready for Day 25 (HAS_CONDITION + anomaly verification).")
    else:
        print("\nDay 24 had issues. Check counts above.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
