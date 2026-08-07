"""
Day 22 verification script.

Run AFTER `docker-compose up -d` to confirm Neo4j is healthy and
create the schema constraints + indexes.

Usage:
    cd data_engineering/neo4j
    python verify_neo4j.py
"""

import sys
from neo4j_connection import get_neo4j_driver, close_driver

CONSTRAINTS = [
    "CREATE CONSTRAINT patient_id_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE",
    "CREATE CONSTRAINT encounter_id_unique IF NOT EXISTS FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE",
    "CREATE CONSTRAINT snomed_code_unique IF NOT EXISTS FOR (c:Condition) REQUIRE c.snomed_code IS UNIQUE",
    "CREATE CONSTRAINT rxnorm_code_unique IF NOT EXISTS FOR (m:Medication) REQUIRE m.rxnorm_code IS UNIQUE",
    "CREATE CONSTRAINT provider_id_unique IF NOT EXISTS FOR (prov:Provider) REQUIRE prov.provider_id IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX encounter_type_idx IF NOT EXISTS FOR (e:Encounter) ON (e.encounter_type)",
    "CREATE INDEX encounter_inpatient_idx IF NOT EXISTS FOR (e:Encounter) ON (e.is_inpatient)",
    "CREATE INDEX encounter_start_time_idx IF NOT EXISTS FOR (e:Encounter) ON (e.start_time)",
    "CREATE INDEX condition_flag_idx IF NOT EXISTS FOR (c:Condition) ON (c.condition_flag)",
    "CREATE INDEX condition_category_idx IF NOT EXISTS FOR (c:Condition) ON (c.clinical_category)",
    "CREATE INDEX medication_flag_idx IF NOT EXISTS FOR (m:Medication) ON (m.medication_flag)",
    "CREATE INDEX medication_class_idx IF NOT EXISTS FOR (m:Medication) ON (m.drug_class)",
    "CREATE INDEX patient_gender_idx IF NOT EXISTS FOR (p:Patient) ON (p.gender)",
    "CREATE INDEX patient_deceased_idx IF NOT EXISTS FOR (p:Patient) ON (p.is_deceased)",
]


def run_schema_setup(session):
    print("\n--- Creating constraints ---")
    for stmt in CONSTRAINTS:
        name = stmt.split("CONSTRAINT ")[1].split(" IF")[0]
        session.run(stmt)
        print(f"  + {name}")

    print("\n--- Creating indexes ---")
    for stmt in INDEXES:
        name = stmt.split("INDEX ")[1].split(" IF")[0]
        session.run(stmt)
        print(f"  + {name}")


def run_smoke_test(session):
    print("\n--- Smoke test ---")
    session.run("CREATE (t:_Test {name: 'day22_verify'}) RETURN t")
    result = session.run(
        "MATCH (t:_Test {name: 'day22_verify'}) RETURN count(t) AS c"
    )
    count = result.single()["c"]
    assert count == 1, f"Smoke test failed: expected 1, got {count}"
    session.run("MATCH (t:_Test {name: 'day22_verify'}) DELETE t")
    print("  + Write + read + delete OK")


def print_db_stats(session):
    print("\n--- Database stats ---")
    result = session.run(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count "
        "ORDER BY count DESC"
    )
    records = list(result)
    if not records:
        print("  (empty database - ready for Day 23 ingestion)")
    else:
        for r in records:
            print(f"  {r['label']}: {r['count']:,}")

    result = session.run(
        "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS count "
        "ORDER BY count DESC"
    )
    records = list(result)
    if records:
        print()
        for r in records:
            print(f"  [{r['rel_type']}]: {r['count']:,}")

    result = session.run("MATCH (n) RETURN count(n) AS nodes")
    nodes = result.single()["nodes"]
    result = session.run("MATCH ()-[r]->() RETURN count(r) AS rels")
    rels = result.single()["rels"]
    print(f"\n  Total: {nodes:,} nodes, {rels:,} relationships")


def main():
    driver = get_neo4j_driver()

    print("--- Verifying Neo4j connectivity ---")
    try:
        driver.verify_connectivity()
        print("  + Connected to Neo4j")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("\n  Is Docker running? Try: docker-compose up -d")
        print("  Then wait 15 seconds for Neo4j to start.")
        sys.exit(1)

    info = driver.get_server_info()
    print(f"  Server: {info.agent}")
    print(f"  Address: {info.address}")

    with driver.session() as session:
        run_schema_setup(session)
        run_smoke_test(session)
        print_db_stats(session)

    close_driver()
    print("\n--- Day 22 verification complete. Ready for Day 23 ingestion. ---\n")


if __name__ == "__main__":
    main()
