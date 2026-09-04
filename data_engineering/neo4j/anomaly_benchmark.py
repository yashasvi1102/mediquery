"""
Day 36: Anomaly Detection Benchmark.

Runs two anomaly detection queries through the GraphRAG agent,
compares results against ground_truth_anomalies in DuckDB,
and computes precision/recall.

Anomaly types:
  1. Warfarin co-prescription (30 injected + 11 baseline = 41 expected)
  
  2. HF 7-day readmission (25 injected + 5 baseline = 30 expected)

Two evaluation modes:
  A. Agent-generated Cypher (tests LLM query quality)
  B. Reference Cypher (tests graph data correctness)


"""

import sys
from pathlib import Path

import duckdb
from neo4j import GraphDatabase
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cypher_few_shots import format_few_shots

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
DUCKDB_PATH = _PROJECT_ROOT / "mediquery.duckdb"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"

OLLAMA_MODEL = "qwen2.5-coder:7b"

# ---------------------------------------------------------------------------
# Graph schema (for agent Cypher generation)
# ---------------------------------------------------------------------------
GRAPH_SCHEMA = """
Node types and properties:
  (:Patient {patient_id, given_name, family_name, gender, age_years_current})
  (:Encounter {encounter_id, encounter_type, is_inpatient, start_time, end_time})
  (:Condition {snomed_code, display, clinical_category, condition_flag})
  (:Medication {rxnorm_code, medication_display, drug_class, medication_flag})

Relationships:
  (:Patient)-[:HAS_ENCOUNTER]->(:Encounter)
  (:Patient)-[:HAS_CONDITION]->(:Condition)
  (:Encounter)-[:DIAGNOSED_WITH]->(:Condition)
  (:Encounter)-[:PRESCRIBED]->(:Medication)

Key: PRESCRIBED goes from Encounter, NOT Patient.
condition_flag values: diabetes_t2, hypertension, heart_failure, copd
"""

CYPHER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a Neo4j Cypher expert. Generate ONLY a Cypher query. No explanation, no markdown, no backticks.

Rules:
- PRESCRIBED goes from Encounter, NOT Patient
- Use CONTAINS for medication name matching
- Return DISTINCT patient_id values — no LIMIT
- For readmissions, compare end_time of first encounter to start_time of second

Schema:
{schema}

{few_shots}"""),
    ("human", "{question}")
])
GROUND_TRUTH_MAP = {
    "warfarin_coprescription": "warfarin_antiplatelet_no_monitoring",
    "hf_7day_readmission": "heart_failure_early_readmission",
}
# ---------------------------------------------------------------------------
# Reference Cypher (gold standard — what we know is correct)
# ---------------------------------------------------------------------------
REFERENCE_QUERIES = {
    "warfarin_coprescription": """
        MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter)-[:PRESCRIBED]->(m1:Medication)
        WHERE m1.medication_display CONTAINS 'Warfarin'
        WITH DISTINCT p
        MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter)-[:PRESCRIBED]->(m2:Medication)
        WHERE m2.medication_display CONTAINS 'Aspirin'
           OR m2.medication_display CONTAINS 'Ibuprofen'
        RETURN DISTINCT p.patient_id AS patient_id
    """,

    "hf_7day_readmission": """
        MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {condition_flag: 'heart_failure'})
        WITH DISTINCT p
        MATCH (p)-[:HAS_ENCOUNTER]->(e1:Encounter {is_inpatient: true})
        MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter {is_inpatient: true})
        WHERE e2.start_time > e1.end_time
          AND e1.encounter_id <> e2.encounter_id
          AND duration.between(e1.end_time, e2.start_time).days <= 7
        RETURN DISTINCT p.patient_id AS patient_id
    """,
}

# NL prompts for the agent
AGENT_QUESTIONS = {
    "warfarin_coprescription":
        "Find ALL patients who have been prescribed both Warfarin and either Aspirin or Ibuprofen. Return all matching patient IDs with no limit.",
    "hf_7day_readmission":
        "Find ALL heart failure patients who had an inpatient readmission within 7 days of a previous inpatient discharge. Return all matching patient IDs with no limit.",
}


# ---------------------------------------------------------------------------
# Load ground truth from DuckDB
# ---------------------------------------------------------------------------
def load_ground_truth(duckdb_conn) -> dict:
    """Load injected anomaly patient IDs from gold.ground_truth_anomalies."""
    truth = {}

    # Try to load from ground_truth_anomalies table
    try:
        df = duckdb_conn.execute("""
            SELECT anomaly_type, patient_id
            FROM gold.ground_truth_anomalies
        """).fetchdf()

        for atype in df["anomaly_type"].unique():
            pids = set(df[df["anomaly_type"] == atype]["patient_id"].tolist())
            truth[atype] = pids
            print(f"  Ground truth [{atype}]: {len(pids)} injected patients")

    except Exception as e:
        print(f"  WARNING: Could not load ground_truth_anomalies: {e}")
        print("  Falling back to anomaly_ prefix detection from Silver")

        # Fallback: find patients with anomaly_ prefixed IDs
        warfarin_pids = duckdb_conn.execute("""
            SELECT DISTINCT patient_id FROM silver.silver_medications
            WHERE medication_request_id LIKE 'anomaly_%'
            AND (medication_display ILIKE '%warfarin%')
        """).fetchdf()["patient_id"].tolist()

        hf_pids = duckdb_conn.execute("""
            SELECT DISTINCT patient_id FROM silver.silver_encounters
            WHERE encounter_id LIKE 'anomaly_%'
        """).fetchdf()["patient_id"].tolist()

        truth["warfarin_coprescription"] = set(warfarin_pids)
        truth["hf_7day_readmission"] = set(hf_pids)
        print(f"  Ground truth [warfarin]: {len(truth['warfarin_coprescription'])} patients (from prefix)")
        print(f"  Ground truth [hf_readmit]: {len(truth['hf_7day_readmission'])} patients (from prefix)")

    return truth


# ---------------------------------------------------------------------------
# Run Cypher and extract patient IDs
# ---------------------------------------------------------------------------
def run_cypher(neo4j_driver, cypher: str) -> set[str]:
    """Execute Cypher and return set of patient_ids."""
    with neo4j_driver.session() as session:
        result = session.run(cypher)
        patient_ids = set()
        for record in result:
            for key, val in record.items():
                if val and isinstance(val, str):
                    patient_ids.add(val)
                    break
        return patient_ids


# ---------------------------------------------------------------------------
# Compute metrics
# ---------------------------------------------------------------------------
def compute_metrics(detected: set, ground_truth_injected: set, label: str) -> dict:
    """Compute precision, recall, F1 against injected anomalies."""
    # True positives: detected AND in ground truth
    tp = detected & ground_truth_injected
    # False positives: detected but NOT in ground truth (could be baseline)
    fp = detected - ground_truth_injected
    # False negatives: in ground truth but NOT detected
    fn = ground_truth_injected - detected

    precision = len(tp) / len(detected) if detected else 0
    recall = len(tp) / len(ground_truth_injected) if ground_truth_injected else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "label": label,
        "detected": len(detected),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_ids": tp,
        "fp_ids": fp,
        "fn_ids": fn,
    }


def print_metrics(m: dict):
    print(f"\n  --- {m['label']} ---")
    print(f"  Detected:         {m['detected']}")
    print(f"  True positives:   {m['true_positives']} (injected anomalies found)")
    print(f"  False positives:  {m['false_positives']} (baseline or noise)")
    print(f"  False negatives:  {m['false_negatives']} (injected anomalies missed)")
    print(f"  Precision:        {m['precision']:.1%}")
    print(f"  Recall:           {m['recall']:.1%}")
    print(f"  F1:               {m['f1']:.1%}")

    if m["false_negatives"] > 0 and m["false_negatives"] <= 5:
        print(f"  Missed IDs:       {m['fn_ids']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Day 36: Anomaly Detection Benchmark\n")

    # Connect
    duckdb_conn = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    neo4j_driver.verify_connectivity()
    print("  Neo4j: connected")
    print("  DuckDB: connected")

    # Load ground truth
    print()
    ground_truth = load_ground_truth(duckdb_conn)

    # ===== MODE A: Reference Cypher (gold standard) =====
    print("\n" + "=" * 60)
    print("MODE A: Reference Cypher (hand-written, known correct)")
    print("=" * 60)

    for anomaly_type, cypher in REFERENCE_QUERIES.items():
        print(f"\n  Running: {anomaly_type}")
        detected = run_cypher(neo4j_driver, cypher)
        print(f"  Detected {len(detected)} patients")

        # Find matching ground truth key
        gt_key = GROUND_TRUTH_MAP.get(anomaly_type)

        if gt_key and ground_truth[gt_key]:
            m = compute_metrics(detected, ground_truth[gt_key], f"Reference: {anomaly_type}")
            print_metrics(m)
        else:
            print(f"  No ground truth found for {anomaly_type}")

    # ===== MODE B: Agent-generated Cypher =====
    print("\n" + "=" * 60)
    print("MODE B: Agent-generated Cypher (LLM via Ollama)")
    print("=" * 60)

    llm = ChatOllama(model=OLLAMA_MODEL, base_url="http://localhost:11434", temperature=0)
    cypher_chain = CYPHER_PROMPT | llm | StrOutputParser()

    for anomaly_type, question in AGENT_QUESTIONS.items():
        print(f"\n  Question: {question}")

        # Generate Cypher
        raw = cypher_chain.invoke({
            "schema": GRAPH_SCHEMA,
            "question": question,
            "few_shots": format_few_shots(),
        })
        cypher = raw.strip().strip("`").strip()
        if cypher.startswith("```"):
            lines = cypher.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cypher = "\n".join(lines).strip()

        print(f"  Generated Cypher:\n    {cypher.replace(chr(10), chr(10) + '    ')}")

        # Execute
        try:
            detected = run_cypher(neo4j_driver, cypher)
            print(f"  Detected {len(detected)} patients")
        except Exception as e:
            print(f"  EXECUTION FAILED: {e}")
            detected = set()

        # Metrics
        gt_key = GROUND_TRUTH_MAP.get(anomaly_type)

        if gt_key and ground_truth[gt_key]:
            m = compute_metrics(detected, ground_truth[gt_key], f"Agent: {anomaly_type}")
            print_metrics(m)
        else:
            print(f"  No ground truth found for {anomaly_type}")

    # ===== Summary =====
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print("""
  Note: Precision against INJECTED anomalies only. 'False positives'
  include baseline patients who genuinely match the pattern (11 baseline
  warfarin, 5 baseline HF). These are true clinical matches, not errors.

  Adjusted precision (injected + baseline as TP) would be higher.
  Report both numbers in interviews — it shows you understand the
  difference between system precision and injection precision.
    """)

    duckdb_conn.close()
    neo4j_driver.close()
    print("Done.")


if __name__ == "__main__":
    main()