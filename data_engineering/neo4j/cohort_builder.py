"""
Day 34: Natural Language Cohort Builder.

User describes a patient population → agent finds matching patients →
runs templated analysis queries → returns structured cohort summary.

The cohort builder differs from regular queries:
  - Regular: "How many diabetics?" → "1,731"
  - Cohort:  "Build a cohort of diabetics over 65" →
      count, gender breakdown, age stats, top conditions,
      top medications, encounter summary, exportable CSV

Usage: imported by graphrag_agent.py, exposed via /cohort command.
    from cohort_builder import CohortBuilder

    builder = CohortBuilder(neo4j_driver, llm)
    result = builder.build("Diabetic patients over 65 with inpatient encounters")
"""

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from neo4j import GraphDatabase

from cypher_few_shots import format_few_shots


# ---------------------------------------------------------------------------
# Schema (same as agent — imported here for self-containment)
# ---------------------------------------------------------------------------
GRAPH_SCHEMA = """
Node types and properties:
  (:Patient {patient_id, given_name, family_name, gender, birth_date, is_deceased, deceased_date, age_years_current, age_at_death, age_group, marital_status, race, ethnicity, city, state, postal_code, country})
  (:Encounter {encounter_id, encounter_type, is_inpatient, start_time, end_time, duration_minutes, length_of_stay_days})
  (:Condition {snomed_code, display, clinical_category, condition_flag, is_billable_diagnosis})
  (:Medication {rxnorm_code, medication_display, drug_class, medication_flag})
  (:Provider {provider_id})

Relationships:
  (:Patient)-[:HAS_ENCOUNTER]->(:Encounter)
  (:Patient)-[:HAS_CONDITION]->(:Condition)
  (:Encounter)-[:DIAGNOSED_WITH]->(:Condition)
  (:Encounter)-[:PRESCRIBED]->(:Medication)
  (:Encounter)-[:TREATED_BY]->(:Provider)

Key enums:
  condition_flag: diabetes_t2, hypertension, heart_failure, copd
  medication_flag: diabetes_drug, antihypertensive, heart_failure_drug, copd_drug
  clinical_category: disorder, finding, situation, unknown
  encounter_type: ambulatory, emergency, inpatient, home_health, virtual
  gender: male, female
"""

# ---------------------------------------------------------------------------
# Prompt: generate a Cypher that returns ONLY patient_ids
# ---------------------------------------------------------------------------
COHORT_CYPHER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a Neo4j Cypher expert. Given the user's cohort definition and the graph schema, generate a Cypher query that returns ONLY patient_id values for matching patients.

Rules:
- The query MUST return p.patient_id and nothing else
- Use DISTINCT to avoid duplicates
- Do NOT add LIMIT — return all matching patients
- PRESCRIBED goes from Encounter, not Patient
- Use CONTAINS for medication/condition name matching
- For chronic conditions use condition_flag
- No markdown, no backticks, no explanation — just raw Cypher

Graph Schema:
{schema}

{few_shots}

Example output format:
MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {{condition_flag: 'diabetes_t2'}})
WHERE p.age_years_current > 65
RETURN DISTINCT p.patient_id"""),
    ("human", "Cohort definition: {definition}")
])


# ---------------------------------------------------------------------------
# Templated analysis queries (not LLM-generated — deterministic)
# ---------------------------------------------------------------------------
ANALYSIS_QUERIES = {
    "total_count": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN count(p) AS total_patients
    """,

    "gender_breakdown": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN p.gender AS gender, count(p) AS count
        ORDER BY count DESC
    """,

    "age_stats": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN
            min(p.age_years_current) AS min_age,
            max(p.age_years_current) AS max_age,
            avg(p.age_years_current) AS avg_age,
            count(CASE WHEN p.is_deceased THEN 1 END) AS deceased_count
    """,

    "age_groups": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN p.age_group AS age_group, count(p) AS count
        ORDER BY count DESC
    """,

    "race_breakdown": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN p.race AS race, count(p) AS count
        ORDER BY count DESC
        LIMIT 10
    """,

    "top_conditions": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})-[:HAS_CONDITION]->(c:Condition)
        WHERE c.clinical_category = 'disorder'
        RETURN c.display AS condition, count(DISTINCT p) AS patient_count
        ORDER BY patient_count DESC
        LIMIT 10
    """,

    "chronic_flags": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})-[:HAS_CONDITION]->(c:Condition)
        WHERE c.condition_flag IS NOT NULL
        RETURN c.condition_flag AS chronic_condition, count(DISTINCT p) AS patient_count
        ORDER BY patient_count DESC
    """,

    "top_medications": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})-[:HAS_ENCOUNTER]->(e:Encounter)-[:PRESCRIBED]->(m:Medication)
        RETURN m.medication_display AS medication, m.drug_class AS drug_class,
               count(DISTINCT p) AS patient_count
        ORDER BY patient_count DESC
        LIMIT 10
    """,

    "encounter_summary": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})-[:HAS_ENCOUNTER]->(e:Encounter)
        RETURN
            e.encounter_type AS encounter_type,
            count(e) AS encounter_count,
            count(DISTINCT p) AS patients_with_type
        ORDER BY encounter_count DESC
    """,

    "patient_list": """
        WITH $patient_ids AS ids
        UNWIND ids AS pid
        MATCH (p:Patient {patient_id: pid})
        RETURN p.patient_id, p.given_name, p.family_name,
               p.gender, p.age_years_current, p.city, p.state,
               p.is_deceased
        ORDER BY p.family_name
        LIMIT 50
    """,
}


# ---------------------------------------------------------------------------
# Cohort result
# ---------------------------------------------------------------------------
@dataclass
class CohortResult:
    definition: str
    cypher: str
    patient_ids: list[str]
    patient_count: int
    demographics: dict = field(default_factory=dict)
    conditions: list[dict] = field(default_factory=list)
    medications: list[dict] = field(default_factory=list)
    encounters: list[dict] = field(default_factory=list)
    patient_list: list[dict] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Cohort builder
# ---------------------------------------------------------------------------
class CohortBuilder:
    def __init__(self, neo4j_driver, llm):
        self.neo4j_driver = neo4j_driver
        self.llm = llm
        self.cypher_chain = COHORT_CYPHER_PROMPT | llm | StrOutputParser()

    def _clean_cypher(self, raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        return cleaned.strip("`").strip()

    def _run_query(self, cypher: str, params: dict = None) -> list[dict]:
        with self.neo4j_driver.session() as session:
            result = session.run(cypher, params or {})
            return [dict(r) for r in result]

    def _run_analysis(self, query_name: str, patient_ids: list[str]) -> list[dict]:
        """Run a templated analysis query against the cohort."""
        cypher = ANALYSIS_QUERIES[query_name]
        try:
            return self._run_query(cypher, {"patient_ids": patient_ids})
        except Exception as e:
            print(f"    WARNING: {query_name} failed — {e}")
            return []

    def build(self, definition: str) -> CohortResult:
        """Build a cohort from a natural language definition."""
        result = CohortResult(definition=definition, cypher="", patient_ids=[], patient_count=0)

        # Step 1: Generate patient-finding Cypher
        raw = self.cypher_chain.invoke({
            "schema": GRAPH_SCHEMA,
            "definition": definition,
            "few_shots": format_few_shots(),
        })
        cypher = self._clean_cypher(raw)
        result.cypher = cypher

        # Step 2: Execute to get patient IDs
        try:
            records = self._run_query(cypher)
        except Exception as e:
            result.error = f"Cypher execution failed: {e}"
            return result

        # Extract patient IDs from results
        patient_ids = []
        for r in records:
            for key, val in r.items():
                if val and isinstance(val, str):
                    patient_ids.append(val)
                    break

        result.patient_ids = patient_ids
        result.patient_count = len(patient_ids)

        if not patient_ids:
            result.error = "No patients matched the cohort definition."
            return result

        # Step 3: Run analysis queries
        print(f"    Analyzing {len(patient_ids)} patients...")

        # Demographics
        gender = self._run_analysis("gender_breakdown", patient_ids)
        age = self._run_analysis("age_stats", patient_ids)
        age_groups = self._run_analysis("age_groups", patient_ids)
        race = self._run_analysis("race_breakdown", patient_ids)

        result.demographics = {
            "gender": gender,
            "age_stats": age[0] if age else {},
            "age_groups": age_groups,
            "race": race,
        }

        # Conditions
        result.conditions = self._run_analysis("top_conditions", patient_ids)
        chronic = self._run_analysis("chronic_flags", patient_ids)
        if chronic:
            result.conditions = chronic + [{"condition": "---top disorders---"}] + result.conditions

        # Medications
        result.medications = self._run_analysis("top_medications", patient_ids)

        # Encounters
        result.encounters = self._run_analysis("encounter_summary", patient_ids)

        # Patient list (first 50)
        result.patient_list = self._run_analysis("patient_list", patient_ids)

        return result

    def export_csv(self, result: CohortResult, output_path: str) -> str:
        """Export cohort patient list to CSV."""
        if not result.patient_list:
            return "No patients to export."

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=result.patient_list[0].keys())
            writer.writeheader()
            writer.writerows(result.patient_list)

        return str(path)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def format_cohort_report(result: CohortResult) -> str:
    """Format a CohortResult as a readable text report."""
    lines = []
    lines.append(f"=== Cohort Report ===")
    lines.append(f"Definition: {result.definition}")
    lines.append(f"Cypher: {result.cypher}")
    lines.append(f"Patients found: {result.patient_count}")

    if result.error:
        lines.append(f"Error: {result.error}")
        return "\n".join(lines)

    # Demographics
    lines.append(f"\n--- Demographics ---")
    demo = result.demographics

    if demo.get("gender"):
        gender_str = ", ".join(f"{r.get('gender', '?')}: {r.get('count', 0)}" for r in demo["gender"])
        lines.append(f"  Gender: {gender_str}")

    age = demo.get("age_stats", {})
    if age:
        min_a = age.get("min_age", "?")
        max_a = age.get("max_age", "?")
        avg_a = age.get("avg_age", "?")
        if isinstance(avg_a, float):
            avg_a = f"{avg_a:.1f}"
        deceased = age.get("deceased_count", 0)
        lines.append(f"  Age: min {min_a}, max {max_a}, avg {avg_a}")
        lines.append(f"  Deceased: {deceased} ({deceased/result.patient_count*100:.1f}%)" if result.patient_count > 0 else "")

    if demo.get("age_groups"):
        groups = ", ".join(f"{r.get('age_group', '?')}: {r.get('count', 0)}" for r in demo["age_groups"])
        lines.append(f"  Age groups: {groups}")

    if demo.get("race"):
        races = ", ".join(f"{r.get('race', '?')}: {r.get('count', 0)}" for r in demo["race"][:5])
        lines.append(f"  Race: {races}")

    # Conditions
    if result.conditions:
        lines.append(f"\n--- Top Conditions ---")
        for r in result.conditions[:10]:
            if r.get("chronic_condition"):
                lines.append(f"  [chronic] {r['chronic_condition']}: {r.get('patient_count', '?')} patients")
            elif r.get("condition") and r["condition"] != "---top disorders---":
                lines.append(f"  {r['condition']}: {r.get('patient_count', '?')} patients")

    # Medications
    if result.medications:
        lines.append(f"\n--- Top Medications ---")
        for r in result.medications[:10]:
            med = r.get("medication", "?")
            cls = r.get("drug_class", "")
            cnt = r.get("patient_count", "?")
            lines.append(f"  {med} ({cls}): {cnt} patients")

    # Encounters
    if result.encounters:
        lines.append(f"\n--- Encounter Summary ---")
        for r in result.encounters:
            etype = r.get("encounter_type", "?")
            ecount = r.get("encounter_count", 0)
            pcount = r.get("patients_with_type", 0)
            lines.append(f"  {etype}: {ecount:,} encounters across {pcount:,} patients")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
TEST_COHORTS = [
    "Diabetic patients over 65",
    "Heart failure patients with inpatient encounters",
    "Female patients with hypertension and diabetes",
]


def main():
    print("Day 34: Cohort Builder Test\n")

    neo4j_driver = GraphDatabase.driver(
        "bolt://localhost:7687", auth=("neo4j", "mediquery2026")
    )
    neo4j_driver.verify_connectivity()
    print("  Neo4j: connected")

    llm = ChatOllama(model="qwen2.5-coder:7b", base_url="http://localhost:11434", temperature=0)
    llm.invoke("test")
    print("  Ollama: connected\n")

    builder = CohortBuilder(neo4j_driver, llm)

    for i, definition in enumerate(TEST_COHORTS, 1):
        print(f"--- Cohort {i}/{len(TEST_COHORTS)}: \"{definition}\" ---")

        result = builder.build(definition)
        report = format_cohort_report(result)
        print(report)

        # Export first cohort as CSV
        if i == 1 and result.patient_list:
            csv_path = builder.export_csv(result, "data_generation/parsed/cohort_export.csv")
            print(f"\n  Exported to: {csv_path}")

        print()

    neo4j_driver.close()
    print("Done.")


if __name__ == "__main__":
    main()