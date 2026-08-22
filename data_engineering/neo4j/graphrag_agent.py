"""
MediQuery GraphRAG Agent v2 — with citation guards + confidence scoring.

Day 30: basic NL → Cypher → answer
Day 31: few-shot examples
Day 32: citation guards + confidence scoring

Citations: every answer must reference specific IDs from the query results.
Post-processing validates cited IDs exist in the result set.

Confidence: scored 0-100 based on query execution, result count, retries.
  >= 70: answer with citations
  40-69: answer with caveat
  < 40:  refuse to answer

Usage:
    python data_engineering/neo4j/graphrag_agent.py          # interactive
    python data_engineering/neo4j/graphrag_agent.py --test   # test suite
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from neo4j import GraphDatabase

from cypher_few_shots import format_few_shots

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_BASE_URL = "http://localhost:11434"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"

MAX_CYPHER_RETRIES = 2

CONFIDENCE_HIGH = 70
CONFIDENCE_LOW = 40

# ---------------------------------------------------------------------------
# Graph schema
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
  - PRESCRIBED goes from Encounter, NOT from Patient
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

CITATION RULES (mandatory):
- Every factual claim MUST reference specific IDs from the results
- For patient-level claims, cite patient_id values: "Patient abc-1234..."
- For encounter-level claims, cite encounter_id values
- For condition claims, cite snomed_code or display values from results
- For medication claims, cite rxnorm_code or medication_display from results
- For aggregate counts, cite the exact number from the results
- If you cannot cite a specific result for a claim, do not make that claim
- NEVER invent or fabricate IDs — use ONLY IDs that appear in the results below

ANSWER RULES:
- Answer the question directly in 2-4 sentences
- Include key numbers from the results
- If results are empty, say "No matching records found" and suggest why
- If the query returned many rows, summarize and cite representative examples
- Do not make claims beyond what the data shows
"""),
    ("human", """Question: {question}

Cypher executed:
{cypher}

Results ({result_count} rows):
{results}

Confidence level: {confidence_label}

Answer:""")
])

CAVEAT_SUFFIX = "\n\nNote: This answer has moderate confidence. The query executed successfully but the results may not fully capture the question's intent. Please verify with more specific queries."

REFUSAL_TEMPLATE = "I'm not confident enough to answer this reliably. {reason} You could try rephrasing the question or asking something more specific about the clinical data."


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
@dataclass
class ConfidenceScore:
    score: int
    label: str
    components: dict


def compute_confidence(
    cypher_generated: bool,
    execution_error: str | None,
    result_count: int,
    retries: int,
) -> ConfidenceScore:
    """
    Score confidence 0-100 based on pipeline health.

    Components:
      - cypher_valid:    30 pts if generated, 0 if not
      - execution:       30 pts if no error, 0 if error
      - results:         25 pts scaled by result count (0 results = 5 pts, 1+ = 25)
      - retry_penalty:   -10 per retry
    """
    components = {}

    components["cypher_valid"] = 30 if cypher_generated else 0

    if execution_error is None:
        components["execution"] = 30
    else:
        components["execution"] = 0

    if result_count == 0:
        components["results"] = 5
    elif result_count <= 3:
        components["results"] = 20
    else:
        components["results"] = 25

    components["retry_penalty"] = -(retries * 10)

    score = max(0, min(100, sum(components.values())))

    if score >= CONFIDENCE_HIGH:
        label = "high"
    elif score >= CONFIDENCE_LOW:
        label = "moderate"
    else:
        label = "low"

    return ConfidenceScore(score=score, label=label, components=components)


# ---------------------------------------------------------------------------
# Citation validation
# ---------------------------------------------------------------------------
def extract_result_ids(results: list[dict]) -> set[str]:
    """Extract all ID-like values from query results for citation validation."""
    ids = set()
    id_keys = {"patient_id", "encounter_id", "snomed_code", "rxnorm_code",
               "provider_id", "condition_id", "medication_request_id"}

    for row in results:
        for key, val in row.items():
            if val is None:
                continue
            # Handle dot-prefixed keys from Neo4j (e.g. p.patient_id)
            clean_key = key.split(".")[-1] if "." in key else key
            if clean_key in id_keys:
                ids.add(str(val))
                if isinstance(val, str) and len(val) >= 8:
                    ids.add(val[:8])
    return ids


def validate_citations(answer: str, valid_ids: set[str]) -> dict:
    """Check if IDs mentioned in the answer exist in the result set."""
    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    id_ref_pattern = r'(?:Patient|Encounter|Provider)\s+([a-f0-9-]{8,})'

    cited = set()

    for pattern in [uuid_pattern, id_ref_pattern]:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            cited.add(match.group(0) if not match.groups() else match.group(1))

    valid = set()
    hallucinated = set()

    for cid in cited:
        if cid in valid_ids or any(vid.startswith(cid[:8]) for vid in valid_ids):
            valid.add(cid)
        else:
            hallucinated.add(cid)

    return {
        "cited_ids": cited,
        "valid_citations": valid,
        "hallucinated_citations": hallucinated,
    }


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
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        cleaned = cleaned.strip("`").strip()
        return cleaned

    def _execute_cypher(self, cypher: str) -> tuple[list[dict], str | None]:
        try:
            with self.neo4j_driver.session() as session:
                result = session.run(cypher)
                records = [dict(r) for r in result]
                return records, None
        except Exception as e:
            return [], str(e)

    def generate_cypher(self, question: str) -> str:
        raw = self.cypher_chain.invoke({
            "schema": GRAPH_SCHEMA,
            "question": question,
            "few_shots": format_few_shots(),
        })
        return self._clean_cypher(raw)

    def query(self, question: str) -> dict:
        result = {
            "question": question,
            "cypher": None,
            "results": [],
            "answer": None,
            "confidence": None,
            "citations": None,
            "error": None,
            "retries": 0,
        }

        # Step 1: Generate Cypher
        cypher = self.generate_cypher(question)
        result["cypher"] = cypher
        cypher_generated = bool(cypher and len(cypher) > 5)

        # Step 2: Execute with retry
        records, error = self._execute_cypher(cypher)

        if error and result["retries"] < MAX_CYPHER_RETRIES:
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

        result["results"] = records
        result["error"] = error

        # Step 3: Compute confidence
        confidence = compute_confidence(
            cypher_generated=cypher_generated,
            execution_error=error,
            result_count=len(records),
            retries=result["retries"],
        )
        result["confidence"] = confidence

        # Step 4: Refuse if low confidence
        if confidence.score < CONFIDENCE_LOW:
            reason = ""
            if error:
                reason = f"The query failed to execute: {error}."
            elif len(records) == 0:
                reason = "The query returned no results."
            else:
                reason = "The confidence score is too low."
            result["answer"] = REFUSAL_TEMPLATE.format(reason=reason)
            return result

        # Step 5: Synthesize answer
        display_results = records[:25]
        results_str = str(display_results) if display_results else "(no results)"

        answer = self.answer_chain.invoke({
            "question": question,
            "cypher": cypher,
            "results": results_str,
            "result_count": len(records),
            "confidence_label": confidence.label,
        })

        # Step 6: Caveat if moderate
        if confidence.label == "moderate":
            answer += CAVEAT_SUFFIX

        result["answer"] = answer

        # Step 7: Validate citations
        valid_ids = extract_result_ids(records)
        citation_report = validate_citations(answer, valid_ids)
        result["citations"] = citation_report

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
    print("=== MediQuery GraphRAG Agent v2 — Test Suite ===")
    print("    (with citation guards + confidence scoring)\n")

    passed = 0
    for i, question in enumerate(TEST_QUERIES, 1):
        print(f"--- Query {i}/{len(TEST_QUERIES)} ---")
        print(f"  Q: {question}")

        result = agent.query(question)

        print(f"  Cypher: {result['cypher']}")

        conf = result["confidence"]
        print(f"  Confidence: {conf.score}/100 ({conf.label}) {conf.components}")

        if result["error"]:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  Results: {len(result['results'])} rows")

        print(f"  Answer: {result['answer']}")

        if result["citations"]:
            c = result["citations"]
            cited = len(c["cited_ids"])
            valid = len(c["valid_citations"])
            hallucinated = len(c["hallucinated_citations"])
            if cited > 0:
                print(f"  Citations: {cited} cited, {valid} valid, {hallucinated} hallucinated")
                if hallucinated > 0:
                    print(f"    HALLUCINATED: {c['hallucinated_citations']}")
            else:
                print(f"  Citations: none (aggregate answer)")

        if result["retries"] > 0:
            print(f"  Retries: {result['retries']}")

        if not result["error"] and conf.score >= CONFIDENCE_LOW:
            passed += 1

        print()

    print(f"=== Results: {passed}/{len(TEST_QUERIES)} answered with sufficient confidence ===")


def interactive_mode(agent: MediQueryAgent):
    print("=== MediQuery GraphRAG Agent v2 ===")
    print("    Citation guards + confidence scoring active")
    print("    Type 'quit' to exit, 'test' to run test suite.\n")

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
        conf = result["confidence"]

        print(f"\n  Cypher: {result['cypher']}")
        print(f"  Confidence: {conf.score}/100 ({conf.label})")

        if result["error"]:
            print(f"  Error: {result['error']}")
        else:
            print(f"  Results: {len(result['results'])} rows")

        print(f"\n  {result['answer']}")

        if result["citations"]:
            c = result["citations"]
            if c["hallucinated_citations"]:
                print(f"\n  WARNING: {len(c['hallucinated_citations'])} hallucinated citation(s) detected.")

        print()


def main():
    print("Initializing MediQuery GraphRAG Agent v2...")
    print(f"  LLM: {OLLAMA_MODEL} via Ollama")
    print(f"  Neo4j: {NEO4J_URI}")
    print(f"  Confidence: refuse <{CONFIDENCE_LOW}, caveat <{CONFIDENCE_HIGH}, answer >={CONFIDENCE_HIGH}")

    agent = MediQueryAgent()

    try:
        agent.neo4j_driver.verify_connectivity()
        print("  Neo4j: connected")
    except Exception as e:
        print(f"  Neo4j: FAILED — {e}")
        sys.exit(1)

    try:
        test = agent.llm.invoke("Return 1")
        print("  Ollama: connected")
    except Exception as e:
        print(f"  Ollama: FAILED — {e}")
        sys.exit(1)

    print()

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_tests(agent)
    else:
        interactive_mode(agent)

    agent.close()
    print("Done.")


if __name__ == "__main__":
    main()