"""
Day 30: MediQuery GraphRAG Agent — core NL → Cypher → answer pipeline.

Two-step chain:
  1. User question + graph schema → LLM generates Cypher
  2. Cypher executes against Neo4j → LLM synthesizes answer

Uses Ollama (qwen2.5-coder:7b) with mandatory schema injection (DD-006).

Usage (from project root):
    python data_engineering/neo4j/graphrag_agent.py

Interactive mode: asks questions in a loop.
"""

import sys
from pathlib import Path
from cypher_few_shots import format_few_shots
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_BASE_URL = "http://localhost:11434"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"

MAX_CYPHER_RETRIES = 2

# ---------------------------------------------------------------------------
# Graph schema (DD-006: mandatory injection for local models)
# ---------------------------------------------------------------------------
GRAPH_SCHEMA = """
Node types and properties:
  (:Patient {patient_id, given_name, family_name, gender, birth_date, is_deceased, deceased_date, age_years_current, age_at_death, age_group, marital_status, race, ethnicity, city, state, postal_code, country})
  (:Encounter {encounter_id, encounter_type, is_inpatient, start_time, end_time, duration_minutes, length_of_stay_days, reason_code, reason_display, type_code, type_display})
  (:Condition {snomed_code, display, clinical_category, condition_flag, is_billable_diagnosis})
  (:Medication {rxnorm_code, medication_display, drug_class, medication_flag})
  (:Provider {provider_id})

Relationship types:
  (:Patient)-[:HAS_ENCOUNTER]->(:Encounter)
  (:Patient)-[:HAS_CONDITION]->(:Condition)  — aggregated: first_onset, latest_onset, episode_count
  (:Encounter)-[:DIAGNOSED_WITH]->(:Condition)  — per-encounter: condition_id, onset_date, abatement_date, clinical_status, is_active
  (:Encounter)-[:PRESCRIBED]->(:Medication)  — per-encounter: medication_request_id, status, authored_on
  (:Encounter)-[:TREATED_BY]->(:Provider)

Key enums:
  condition_flag: diabetes_t2, hypertension, heart_failure, copd (or NULL)
  medication_flag: diabetes_drug, antihypertensive, heart_failure_drug, copd_drug (or NULL)
  clinical_category: disorder, finding, situation, unknown
  encounter_type: ambulatory, emergency, inpatient, home_health, virtual
  gender: male, female
  drug_class: biguanide, insulin, ace_inhibitor, statin, opioid, antibiotic, vaccine, analgesic, etc.

Important notes:
  - Use condition_flag to find chronic disease cohorts, not display text
  - is_inpatient is a boolean on Encounter nodes
  - HAS_CONDITION is a direct Patient→Condition link (fast cohort queries)
  - DIAGNOSED_WITH goes through Encounter (for temporal context)
  - All timestamps are datetime type
  - patient_id, encounter_id, snomed_code, rxnorm_code, provider_id are unique
"""

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
CYPHER_GENERATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a Neo4j Cypher expert. Given the user's question and the graph schema below, generate ONLY a Cypher query. No explanation, no markdown, no backticks — just the raw Cypher.

Rules:
- Use only node labels, relationship types, and properties from the schema
- PRESCRIBED and DIAGNOSED_WITH relationships go from Encounter, NOT from Patient
- To find medications for a patient: (Patient)-[:HAS_ENCOUNTER]->(Encounter)-[:PRESCRIBED]->(Medication)
- To find conditions diagnosed in an encounter: (Encounter)-[:DIAGNOSED_WITH]->(Condition)
- For direct patient-condition links use: (Patient)-[:HAS_CONDITION]->(Condition)
- Use CONTAINS for medication and condition name matching, not exact equality
- Always RETURN specific properties, not entire nodes
- Use DISTINCT when counting patients to avoid duplicates
- Limit results to 25 rows unless the user asks for all
- For chronic conditions, use condition_flag (e.g. 'diabetes_t2')
- For general "most common condition" queries, use display with clinical_category = 'disorder'
- For encounter counts, count encounters not patients
- Use count() for "how many" questions

Graph Schema:
{schema}

{few_shots}"""),
    ("human", "{question}")
])

ANSWER_SYNTHESIS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a clinical data analyst. Given the user's question, the Cypher query that was executed, and the results, provide a clear answer.

Rules:
- Answer the question directly in 1-3 sentences
- Include the key numbers from the results
- If results are empty, say so clearly
- Mention the patient_ids or encounter_ids in the results as citations (e.g. "Patient abc-123")
- Do not make claims beyond what the data shows
- If the query returned many rows, summarize rather than listing all
"""),
    ("human", """Question: {question}

Cypher executed:
{cypher}

Results:
{results}

Answer:""")
])


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class MediQueryAgent:
    def __init__(self):
        self.llm = ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0,
        )
        self.neo4j_driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        self.cypher_chain = CYPHER_GENERATION_PROMPT | self.llm | StrOutputParser()
        self.answer_chain = ANSWER_SYNTHESIS_PROMPT | self.llm | StrOutputParser()

    def _clean_cypher(self, raw: str) -> str:
        """Strip markdown fences and whitespace from LLM output."""
        cleaned = raw.strip()
        # Remove ```cypher ... ``` wrapping
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```cypher) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        # Remove any leading/trailing backticks
        cleaned = cleaned.strip("`").strip()
        return cleaned

    def _execute_cypher(self, cypher: str) -> tuple[list[dict], str | None]:
        """Execute Cypher against Neo4j. Returns (results, error)."""
        try:
            with self.neo4j_driver.session() as session:
                result = session.run(cypher)
                records = [dict(r) for r in result]
                return records, None
        except Exception as e:
            return [], str(e)

    def generate_cypher(self, question: str) -> str:
        """Generate Cypher from natural language question."""
        raw = self.cypher_chain.invoke({
    "schema": GRAPH_SCHEMA,
    "question": question,
    "few_shots": format_few_shots(),
})
        return self._clean_cypher(raw)

    def query(self, question: str) -> dict:
        """
        Full pipeline: question → Cypher → execute → answer.

        Returns dict with:
            question, cypher, results, answer, error, retries
        """
        result = {
            "question": question,
            "cypher": None,
            "results": [],
            "answer": None,
            "error": None,
            "retries": 0,
        }

        # Step 1: Generate Cypher
        cypher = self.generate_cypher(question)
        result["cypher"] = cypher

        # Step 2: Execute with retry
        records, error = self._execute_cypher(cypher)

        if error and result["retries"] < MAX_CYPHER_RETRIES:
            # Retry: feed the error back to the LLM
            retry_question = (
                f"{question}\n\n"
                f"The previous Cypher query failed with this error: {error}\n"
                f"The failed query was: {cypher}\n"
                f"Generate a corrected Cypher query."
            )
            for attempt in range(MAX_CYPHER_RETRIES):
                result["retries"] += 1
                cypher = self.generate_cypher(retry_question)
                result["cypher"] = cypher
                records, error = self._execute_cypher(cypher)
                if not error:
                    break

        if error:
            result["error"] = error
            result["answer"] = f"I couldn't execute the query. Error: {error}"
            return result

        result["results"] = records

        # Step 3: Synthesize answer
        # Truncate results for the LLM context (7B model has limited context)
        display_results = records[:25]
        results_str = str(display_results) if display_results else "(no results)"

        answer = self.answer_chain.invoke({
            "question": question,
            "cypher": cypher,
            "results": results_str,
        })
        result["answer"] = answer

        return result

    def close(self):
        self.neo4j_driver.close()


# ---------------------------------------------------------------------------
# Test queries
# ---------------------------------------------------------------------------
TEST_QUERIES = [
    "How many patients have diabetes?",
    "How many patients are female?",
    "List 5 patients with heart failure and their ages",
    "What is the most common encounter type?",
    "How many inpatient encounters are in the database?",
    "Show me patients prescribed both warfarin and aspirin",
    "Which condition has the most patients?",
    "How many providers are in the database?",
]


def run_tests(agent: MediQueryAgent):
    """Run test queries and display results."""
    print("=== MediQuery GraphRAG Agent — Test Suite ===\n")

    for i, question in enumerate(TEST_QUERIES, 1):
        print(f"--- Query {i}/{len(TEST_QUERIES)} ---")
        print(f"  Q: {question}")

        result = agent.query(question)

        print(f"  Cypher: {result['cypher']}")

        if result["error"]:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  Results: {len(result['results'])} rows")
            print(f"  Answer: {result['answer']}")

        if result["retries"] > 0:
            print(f"  (retried {result['retries']} time(s))")

        print()


def interactive_mode(agent: MediQueryAgent):
    """Interactive question-answer loop."""
    print("=== MediQuery GraphRAG Agent — Interactive Mode ===")
    print("Type 'quit' to exit, 'test' to run test suite.\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            break
        if question.lower() == "test":
            run_tests(agent)
            continue

        result = agent.query(question)

        print(f"\n  Cypher: {result['cypher']}")
        if result["error"]:
            print(f"  Error: {result['error']}")
        else:
            print(f"  Results: {len(result['results'])} rows")
        print(f"\n  {result['answer']}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Initializing MediQuery GraphRAG Agent...")
    print(f"  LLM: {OLLAMA_MODEL} via Ollama")
    print(f"  Neo4j: {NEO4J_URI}")

    agent = MediQueryAgent()

    # Verify connections
    try:
        agent.neo4j_driver.verify_connectivity()
        print("  Neo4j: connected")
    except Exception as e:
        print(f"  Neo4j: FAILED — {e}")
        print("  Is Docker running? Try: docker compose up -d")
        sys.exit(1)

    # Quick LLM check
    try:
        test = agent.llm.invoke("Return 1")
        print("  Ollama: connected")
    except Exception as e:
        print(f"  Ollama: FAILED — {e}")
        print("  Is Ollama running? Check the tray icon.")
        sys.exit(1)

    print()

    # Run mode
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_tests(agent)
    else:
        interactive_mode(agent)

    agent.close()
    print("Done.")


if __name__ == "__main__":
    main()