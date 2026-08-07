"""
Day 23: Ingest Patient and Encounter nodes + HAS_ENCOUNTER relationships.

Reads from DuckDB silver tables, writes to Neo4j via batched UNWIND + MERGE.
Idempotent: safe to rerun (MERGE won't duplicate, SET overwrites properties).

Usage (from project root):
    python -m data_engineering.neo4j.ingest_patients_encounters

Or:
    cd data_engineering/neo4j
    python ingest_patients_encounters.py

Requires:
    - Neo4j running (docker compose up -d)
    - mediquery.duckdb at project root with silver schema populated
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

# Resolve DuckDB path — works whether run from project root or from this dir
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent  # data_engineering/neo4j -> data_engineering -> root
DUCKDB_PATH = _PROJECT_ROOT / "mediquery.duckdb"

# Neo4j connection (matches docker-compose.yml)
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_duckdb_conn():
    if not DUCKDB_PATH.exists():
        print(f"ERROR: DuckDB not found at {DUCKDB_PATH}")
        print("Run this from the project root or check the path.")
        sys.exit(1)
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def clean_batch(records: list[dict]) -> list[dict]:
    """Convert pandas NaT/NaN to None so Neo4j driver doesn't choke."""
    for row in records:
        for key, val in row.items():
            if pd.isna(val):
                row[key] = None
    return records


def batched(records: list[dict], size: int):
    """Yield successive chunks of `size` from records."""
    for i in range(0, len(records), size):
        yield records[i : i + size]


def run_batched_ingest(session, cypher: str, records: list[dict], label: str):
    """Run a batched UNWIND ingestion with progress reporting."""
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
    print(f"\r  {label}: {total:,} rows ingested in {elapsed:.1f}s" + " " * 20)


# ---------------------------------------------------------------------------
# Phase 1: Patient nodes
# ---------------------------------------------------------------------------
PATIENT_CYPHER = """
UNWIND $batch AS row
MERGE (p:Patient {patient_id: row.patient_id})
SET p.given_name       = row.given_name,
    p.family_name      = row.family_name,
    p.gender           = row.gender,
    p.birth_date       = date(row.birth_date),
    p.is_deceased      = row.is_deceased,
    p.deceased_date    = row.deceased_date,
    p.age_years_current = row.age_years_current,
    p.age_at_death     = row.age_at_death,
    p.age_group        = row.age_group,
    p.marital_status   = row.marital_status,
    p.race             = row.race,
    p.ethnicity        = row.ethnicity,
    p.city             = row.city,
    p.state            = row.state,
    p.postal_code      = row.postal_code,
    p.country          = row.country
"""


def ingest_patients(neo4j_session, duckdb_conn):
    print("\n=== Phase 1: Patient nodes ===")

    df = duckdb_conn.execute("""
        SELECT patient_id, given_name, family_name, gender,
               birth_date, deceased_date, is_deceased,
               age_years_current, age_at_death, age_group,
               marital_status, race, ethnicity,
               city, state, postal_code, country
        FROM silver.silver_patients
    """).fetchdf()

    # birth_date needs to be ISO string for Neo4j date() function
    df["birth_date"] = df["birth_date"].apply(
        lambda x: str(x)[:10] if pd.notna(x) else None
    )

    records = df.to_dict("records")
    run_batched_ingest(neo4j_session, PATIENT_CYPHER, records, "Patients")

    # Verify
    result = neo4j_session.run("MATCH (p:Patient) RETURN count(p) AS cnt")
    actual = result.single()["cnt"]
    expected = len(records)
    status = "PASS" if actual == expected else "FAIL"
    print(f"  Verify: {actual:,} Patient nodes ({status}, expected {expected:,})")
    return actual == expected


# ---------------------------------------------------------------------------
# Phase 2: Encounter nodes + HAS_ENCOUNTER relationships
# ---------------------------------------------------------------------------
ENCOUNTER_CYPHER = """
UNWIND $batch AS row
MATCH (p:Patient {patient_id: row.patient_id})
MERGE (e:Encounter {encounter_id: row.encounter_id})
SET e.encounter_type     = row.encounter_type,
    e.is_inpatient       = row.is_inpatient,
    e.start_time         = row.start_time,
    e.end_time           = row.end_time,
    e.duration_minutes   = row.duration_minutes,
    e.length_of_stay_days = row.length_of_stay_days,
    e.reason_code        = row.reason_code,
    e.reason_display     = row.reason_display,
    e.type_code          = row.type_code,
    e.type_display       = row.type_display
MERGE (p)-[:HAS_ENCOUNTER]->(e)
"""


def ingest_encounters(neo4j_session, duckdb_conn):
    print("\n=== Phase 2: Encounter nodes + HAS_ENCOUNTER ===")

    df = duckdb_conn.execute("""
        SELECT encounter_id, patient_id,
               encounter_type, is_inpatient,
               start_time, end_time,
               duration_minutes, length_of_stay_days,
               reason_code, reason_display,
               type_code, type_display
        FROM silver.silver_encounters
    """).fetchdf()

    records = df.to_dict("records")
    run_batched_ingest(neo4j_session, ENCOUNTER_CYPHER, records, "Encounters + HAS_ENCOUNTER")

    # Verify nodes
    result = neo4j_session.run("MATCH (e:Encounter) RETURN count(e) AS cnt")
    enc_count = result.single()["cnt"]
    expected_enc = len(records)
    enc_ok = enc_count == expected_enc
    print(f"  Verify: {enc_count:,} Encounter nodes ({'PASS' if enc_ok else 'FAIL'}, expected {expected_enc:,})")

    # Verify relationships
    result = neo4j_session.run("MATCH ()-[r:HAS_ENCOUNTER]->() RETURN count(r) AS cnt")
    rel_count = result.single()["cnt"]
    rel_ok = rel_count == expected_enc
    print(f"  Verify: {rel_count:,} HAS_ENCOUNTER rels ({'PASS' if rel_ok else 'FAIL'}, expected {expected_enc:,})")

    # Check for orphans (encounters with no patient match)
    result = neo4j_session.run("""
        MATCH (e:Encounter)
        WHERE NOT ()-[:HAS_ENCOUNTER]->(e)
        RETURN count(e) AS orphans
    """)
    orphans = result.single()["orphans"]
    if orphans > 0:
        print(f"  WARNING: {orphans:,} orphan Encounter nodes (no HAS_ENCOUNTER)")

    return enc_ok and rel_ok


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(neo4j_session):
    print("\n=== Day 23 Summary ===")
    result = neo4j_session.run("""
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS count
        ORDER BY count DESC
    """)
    for r in result:
        print(f"  {r['label']}: {r['count']:,}")

    result = neo4j_session.run("""
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS count
        ORDER BY count DESC
    """)
    for r in result:
        print(f"  [{r['rel_type']}]: {r['count']:,}")

    result = neo4j_session.run("MATCH (n) RETURN count(n) AS n")
    nodes = result.single()["n"]
    result = neo4j_session.run("MATCH ()-[r]->() RETURN count(r) AS r")
    rels = result.single()["r"]
    print(f"\n  Total: {nodes:,} nodes, {rels:,} relationships")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Day 23: Ingesting Patients + Encounters into Neo4j")
    print(f"DuckDB: {DUCKDB_PATH}")
    print(f"Neo4j:  {NEO4J_URI}")

    duckdb_conn = get_duckdb_conn()
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    neo4j_driver.verify_connectivity()
    print("Connected to both databases.\n")

    all_passed = True
    with neo4j_driver.session() as session:
        if not ingest_patients(session, duckdb_conn):
            all_passed = False

        if not ingest_encounters(session, duckdb_conn):
            all_passed = False

        print_summary(session)

    duckdb_conn.close()
    neo4j_driver.close()

    if all_passed:
        print("\nDay 23 PASSED. Ready for Day 24 (Conditions + Medications + Providers).")
    else:
        print("\nDay 23 FAILED. Check counts above and investigate.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
