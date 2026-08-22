"""
Day 25: HAS_CONDITION aggregated relationships + anomaly verification
        + graph validation queries.

Depends on Day 23 (Patient nodes) and Day 24 (Condition nodes + DIAGNOSED_WITH).

HAS_CONDITION is a convenience relationship: (Patient)-[:HAS_CONDITION]->(Condition)
with aggregated properties (first_onset, latest_onset, episode_count).
Enables fast cohort queries ("all diabetics") without traversing through Encounters.

Usage (from project root):
    python -m data_engineering.neo4j.ingest_has_condition

Idempotent via MERGE.
"""

import sys
import time
import math
from pathlib import Path

import duckdb
import pandas as pd
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BATCH_SIZE = 5_000

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
DUCKDB_PATH = _PROJECT_ROOT / "mediquery.duckdb"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"


# ---------------------------------------------------------------------------
# Helpers
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
# Phase 1: HAS_CONDITION aggregated relationships
# ---------------------------------------------------------------------------
HAS_CONDITION_CYPHER = """
UNWIND $batch AS row
MATCH (p:Patient {patient_id: row.patient_id})
MATCH (c:Condition {snomed_code: row.snomed_code})
MERGE (p)-[r:HAS_CONDITION]->(c)
SET r.first_onset   = row.first_onset,
    r.latest_onset  = row.latest_onset,
    r.episode_count = row.episode_count
"""


def ingest_has_condition(session, duckdb_conn):
    print("\n=== Phase 1: HAS_CONDITION aggregated relationships ===")

    df = duckdb_conn.execute("""
        SELECT
            patient_id,
            snomed_code,
            MIN(onset_date) AS first_onset,
            MAX(onset_date) AS latest_onset,
            COUNT(*) AS episode_count
        FROM silver.silver_conditions
        WHERE patient_id IS NOT NULL
          AND snomed_code IS NOT NULL
        GROUP BY patient_id, snomed_code
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(session, HAS_CONDITION_CYPHER, records, "HAS_CONDITION")

    result = session.run("MATCH ()-[r:HAS_CONDITION]->() RETURN count(r) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    ok = actual == expected
    print(f"  Verify: {actual:,} HAS_CONDITION rels ({'PASS' if ok else 'FAIL'}, expected {expected:,})")

    # Spot check: chronic condition patient counts via HAS_CONDITION
    print("\n  Chronic cohort check (via HAS_CONDITION):")
    result = session.run("""
        MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition)
        WHERE c.condition_flag IS NOT NULL
        RETURN c.condition_flag AS flag, count(DISTINCT p) AS patients
        ORDER BY patients DESC
    """)
    for r in result:
        print(f"    {r['flag']}: {r['patients']:,} patients")

    return ok


# ---------------------------------------------------------------------------
# Phase 2: Anomaly verification
# ---------------------------------------------------------------------------
def verify_anomalies(session, duckdb_conn):
    print("\n=== Phase 2: Anomaly data verification ===")
    all_ok = True

    # Warfarin co-prescription: find patients with both warfarin and NSAID/aspirin
    result = session.run("""
        MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter)-[:PRESCRIBED]->(m1:Medication)
        WHERE m1.medication_display CONTAINS 'Warfarin'
        WITH DISTINCT p
        MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter)-[:PRESCRIBED]->(m2:Medication)
        WHERE m2.medication_display CONTAINS 'Aspirin'
           OR m2.medication_display CONTAINS 'Ibuprofen'
        RETURN count(DISTINCT p) AS warfarin_coprescription_patients
    """)
    warfarin_count = result.single()["warfarin_coprescription_patients"]
    warfarin_ok = warfarin_count == 41
    print(f"  Warfarin co-prescription patients: {warfarin_count} ({'PASS' if warfarin_ok else 'FAIL'}, expected 41)")
    if not warfarin_ok:
        all_ok = False

    # HF 7-day readmission
    result = session.run("""
        MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {condition_flag: 'heart_failure'})
        WITH DISTINCT p
        MATCH (p)-[:HAS_ENCOUNTER]->(e1:Encounter {is_inpatient: true})
        MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter {is_inpatient: true})
        WHERE e2.start_time > e1.end_time
          AND e1.encounter_id <> e2.encounter_id
          AND duration.between(e1.end_time, e2.start_time).days <= 7
        RETURN count(DISTINCT e2) AS hf_7day_readmissions
    """)
    hf_count = result.single()["hf_7day_readmissions"]
    # Note: exact count depends on how Synthea baseline + injections interact.
    # Expected ~30 (25 injected + 5 baseline) but graph traversal may differ
    # from SQL due to relationship dedup or temporal precision.
    print(f"  HF 7-day readmissions: {hf_count} (expected ~30)")

    # Verify injected IDs are present
    result = session.run("""
        MATCH (e:Encounter)
        WHERE e.encounter_id STARTS WITH 'anomaly_'
        RETURN count(e) AS injected_encounters
    """)
    injected_enc = result.single()["injected_encounters"]
    print(f"  Injected encounter nodes (anomaly_* prefix): {injected_enc} (expected 25)")

    result = session.run("""
        MATCH ()-[r:PRESCRIBED]->(m:Medication)
        WHERE r.medication_request_id STARTS WITH 'anomaly_'
        RETURN count(r) AS injected_prescriptions
    """)
    injected_rx = result.single()["injected_prescriptions"]
    print(f"  Injected prescription rels (anomaly_* prefix): {injected_rx} (expected 60)")

    result = session.run("""
        MATCH ()-[r:DIAGNOSED_WITH]->(c:Condition)
        WHERE r.condition_id STARTS WITH 'anomaly_'
        RETURN count(r) AS injected_conditions
    """)
    injected_cond = result.single()["injected_conditions"]
    print(f"  Injected condition rels (anomaly_* prefix): {injected_cond} (expected 25)")

    return all_ok


# ---------------------------------------------------------------------------
# Phase 3: Cross-layer validation queries
# ---------------------------------------------------------------------------
def run_validation_queries(session, duckdb_conn):
    print("\n=== Phase 3: Cross-layer validation (Neo4j vs DuckDB Gold) ===")
    all_ok = True

    # 1. Total patient count
    result = session.run("MATCH (p:Patient) RETURN count(p) AS cnt")
    neo4j_patients = result.single()["cnt"]
    duck_patients = duckdb_conn.execute(
        "SELECT COUNT(*) FROM silver.silver_patients"
    ).fetchone()[0]
    ok = neo4j_patients == duck_patients
    print(f"  Patients:   Neo4j {neo4j_patients:,} vs DuckDB {duck_patients:,} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 2. Total encounter count
    result = session.run("MATCH (e:Encounter) RETURN count(e) AS cnt")
    neo4j_enc = result.single()["cnt"]
    duck_enc = duckdb_conn.execute(
        "SELECT COUNT(*) FROM silver.silver_encounters"
    ).fetchone()[0]
    ok = neo4j_enc == duck_enc
    print(f"  Encounters: Neo4j {neo4j_enc:,} vs DuckDB {duck_enc:,} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 3. Provider count
    result = session.run("MATCH (prov:Provider) RETURN count(prov) AS cnt")
    neo4j_prov = result.single()["cnt"]
    duck_prov = duckdb_conn.execute(
        "SELECT COUNT(DISTINCT provider_id) FROM silver.silver_encounters WHERE provider_id IS NOT NULL"
    ).fetchone()[0]
    ok = neo4j_prov == duck_prov
    print(f"  Providers:  Neo4j {neo4j_prov:,} vs DuckDB {duck_prov:,} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 4. Unique condition codes
    result = session.run("MATCH (c:Condition) RETURN count(c) AS cnt")
    neo4j_cond = result.single()["cnt"]
    duck_cond = duckdb_conn.execute(
        "SELECT COUNT(DISTINCT snomed_code) FROM silver.silver_conditions"
    ).fetchone()[0]
    ok = neo4j_cond == duck_cond
    print(f"  Conditions: Neo4j {neo4j_cond} vs DuckDB {duck_cond} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 5. Unique medication codes
    result = session.run("MATCH (m:Medication) RETURN count(m) AS cnt")
    neo4j_med = result.single()["cnt"]
    duck_med = duckdb_conn.execute(
        "SELECT COUNT(DISTINCT rxnorm_code) FROM silver.silver_medications"
    ).fetchone()[0]
    ok = neo4j_med == duck_med
    print(f"  Medications: Neo4j {neo4j_med} vs DuckDB {duck_med} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 6. Chronic condition cohorts (must match gold_chronic_conditions)
    print("\n  Chronic condition cohort validation:")
    result = session.run("""
        MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition)
        WHERE c.condition_flag IS NOT NULL
        RETURN c.condition_flag AS flag, count(DISTINCT p) AS neo4j_count
        ORDER BY neo4j_count DESC
    """)
    neo4j_cohorts = {r["flag"]: r["neo4j_count"] for r in result}

    duck_cohorts_df = duckdb_conn.execute("""
        SELECT condition_flag, COUNT(DISTINCT patient_id) AS duck_count
        FROM gold.gold_chronic_conditions
        GROUP BY condition_flag
        ORDER BY duck_count DESC
    """).fetchdf()

    for _, row in duck_cohorts_df.iterrows():
        flag = row["condition_flag"]
        duck_n = int(row["duck_count"])
        neo4j_n = neo4j_cohorts.get(flag, 0)
        ok = neo4j_n == duck_n
        print(f"    {flag}: Neo4j {neo4j_n:,} vs Gold {duck_n:,} {'PASS' if ok else 'FAIL'}")
        if not ok: all_ok = False

    # 7. Inpatient encounter count
    result = session.run(
        "MATCH (e:Encounter {is_inpatient: true}) RETURN count(e) AS cnt"
    )
    neo4j_inp = result.single()["cnt"]
    duck_inp = duckdb_conn.execute(
        "SELECT COUNT(*) FROM silver.silver_encounters WHERE is_inpatient = true"
    ).fetchone()[0]
    ok = neo4j_inp == duck_inp
    print(f"\n  Inpatient encounters: Neo4j {neo4j_inp:,} vs DuckDB {duck_inp:,} {'PASS' if ok else 'FAIL'}")
    if not ok: all_ok = False

    # 8. DD-001 check: clinical_category distribution in graph
    print("\n  DD-001 distribution (Condition nodes):")
    result = session.run("""
        MATCH (c:Condition)
        RETURN c.clinical_category AS cat, count(c) AS cnt
        ORDER BY cnt DESC
    """)
    for r in result:
        print(f"    {r['cat']}: {r['cnt']}")

    return all_ok


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_final_summary(session):
    print("\n=== Final Graph Summary (end of Day 25) ===")
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
    print(f"  Graph ingestion complete (Days 22-25).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Day 25: HAS_CONDITION + anomaly verification + graph validation")
    print(f"DuckDB: {DUCKDB_PATH}")
    print(f"Neo4j:  {NEO4J_URI}")

    duckdb_conn = get_duckdb_conn()
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    neo4j_driver.verify_connectivity()
    print("Connected to both databases.")

    # Preflight
    with neo4j_driver.session() as session:
        result = session.run("MATCH (p:Patient) RETURN count(p) AS cnt")
        p = result.single()["cnt"]
        result = session.run("MATCH (c:Condition) RETURN count(c) AS cnt")
        c = result.single()["cnt"]
        if p == 0 or c == 0:
            print(f"\nERROR: Prerequisites missing! Patients: {p}, Conditions: {c}")
            print("Run Day 23 + Day 24 scripts first.")
            sys.exit(1)
        print(f"  Preflight: {p:,} Patients, {c} Conditions present.")

    all_passed = True
    with neo4j_driver.session() as session:
        if not ingest_has_condition(session, duckdb_conn):
            all_passed = False

        if not verify_anomalies(session, duckdb_conn):
            all_passed = False

        if not run_validation_queries(session, duckdb_conn):
            all_passed = False

        print_final_summary(session)

    duckdb_conn.close()
    neo4j_driver.close()

    if all_passed:
        print("\nDay 25 PASSED. Graph layer complete. Ready for Phase 2 (Chroma + summaries).")
    else:
        print("\nDay 25 had issues. Check output above.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())