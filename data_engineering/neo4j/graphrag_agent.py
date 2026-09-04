"""
MediQuery GraphRAG Agent v3 — hybrid retrieval.

Day 30: NL → Cypher → answer
Day 31: few-shot examples
Day 32: citation guards + confidence scoring
Day 33: hybrid retrieval (router + Chroma + Neo4j)

Query router classifies questions:
  structured → Cypher against Neo4j
  semantic   → vector search against Chroma
  hybrid     → Cypher filter + Chroma ranking
  off_topic  → refuse

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
import chromadb
from chromadb.utils import embedding_functions

from cypher_few_shots import format_few_shots
from query_router import classify_query

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_BASE_URL = "http://localhost:11434"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "mediquery2026"

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")
COLLECTION_NAME = "patient_summaries"

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
  (:Encounter)-[:DIAGNOSED_WITH]->(:Condition)  — per-encounter: condition_id, onset_date, abatement_date
  (:Encounter)-[:PRESCRIBED]->(:Medication)  — per-encounter: medication_request_id, status, authored_on
  (:Encounter)-[:TREATED_BY]->(:Provider)

Key enums:
  condition_flag: diabetes_t2, hypertension, heart_failure, copd (or NULL)
  medication_flag: diabetes_drug, antihypertensive, heart_failure_drug, copd_drug (or NULL)
  clinical_category: disorder, finding, situation, unknown
  encounter_type: ambulatory, emergency, inpatient, home_health, virtual
  gender: male, female
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
- For direct patient-condition links use: (Patient)-[:HAS_CONDITION]->(Condition)
- Use CONTAINS for medication and condition name matching, not exact equality
- Always RETURN specific properties, not entire nodes
- Use DISTINCT when counting patients to avoid duplicates
- Limit results to 25 rows unless the user asks for all
- For chronic conditions, use condition_flag (e.g. 'diabetes_t2')
- For general "most common condition" queries, use display with clinical_category = 'disorder'
- For encounter counts, count encounters not patients

Graph Schema:
{schema}

{few_shots}"""),
    ("human", "{question}")
])

ANSWER_SYNTHESIS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a clinical data analyst. Given the user's question, the Cypher query that was executed, and the results, provide a clear answer.

CITATION RULES (mandatory):
- Every factual claim MUST reference specific IDs from the results
- For patient-level claims, cite patient_id values
- For aggregate counts, cite the exact number from results
- NEVER invent or fabricate IDs — use ONLY IDs from the results below

ANSWER RULES:
- Answer directly in 2-4 sentences
- Include key numbers from results
- If results are empty, say "No matching records found"
- If many rows, summarize and cite representative examples
"""),
    ("human", """Question: {question}

Cypher executed:
{cypher}

Results ({result_count} rows):
{results}

Confidence level: {confidence_label}

Answer:""")
])

SEMANTIC_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a clinical data analyst. Given the user's question and matching patient profiles from a semantic search, provide a clear answer.

Rules:
- Summarize the matching patient profiles
- Cite patient IDs from the results
- Note the similarity scores (lower distance = better match)
- Answer in 2-4 sentences
- Do not make claims beyond what the patient profiles show
"""),
    ("human", """Question: {question}

Matching patient profiles (top {result_count}, by similarity):
{results}

Answer:""")
])

HYBRID_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a clinical data analyst. The user's question was answered using both a structured database query and a semantic search.

Rules:
- Combine insights from both sources
- Cite patient IDs where available
- Note the structured count and the semantic profile matches
- Answer in 3-5 sentences
- Clearly distinguish what came from structured data vs semantic matching
"""),
    ("human", """Question: {question}

Structured query results ({structured_count} rows):
{structured_results}

Semantic search results (top {semantic_count} matches):
{semantic_results}

Answer:""")
])

CAVEAT_SUFFIX = "\n\nNote: This answer has moderate confidence. Please verify with more specific queries."
REFUSAL_TEMPLATE = "I'm not confident enough to answer this reliably. {reason} You could try rephrasing the question."
OFFTOPIC_RESPONSE = "This question is outside the scope of the clinical dataset. I can answer questions about patients, conditions, medications, encounters, and providers in the MediQuery database."


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
@dataclass
class ConfidenceScore:
    score: int
    label: str
    components: dict


def compute_confidence(
    cypher_generated: bool = True,
    execution_error: str | None = None,
    result_count: int = 0,
    retries: int = 0,
) -> ConfidenceScore:
    components = {}
    components["cypher_valid"] = 30 if cypher_generated else 0
    components["execution"] = 30 if execution_error is None else 0
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
    ids = set()
    id_keys = {"patient_id", "encounter_id", "snomed_code", "rxnorm_code",
               "provider_id", "condition_id", "medication_request_id"}
    for row in results:
        for key, val in row.items():
            if val is None:
                continue
            clean_key = key.split(".")[-1] if "." in key else key
            if clean_key in id_keys:
                ids.add(str(val))
                if isinstance(val, str) and len(val) >= 8:
                    ids.add(val[:8])
    return ids


def validate_citations(answer: str, valid_ids: set[str]) -> dict:
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

        # Chroma
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.collection = chroma_client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )

        # Chains
        self.cypher_chain = CYPHER_GENERATION_PROMPT | self.llm | StrOutputParser()
        self.answer_chain = ANSWER_SYNTHESIS_PROMPT | self.llm | StrOutputParser()
        self.semantic_chain = SEMANTIC_ANSWER_PROMPT | self.llm | StrOutputParser()
        self.hybrid_chain = HYBRID_ANSWER_PROMPT | self.llm | StrOutputParser()

    def _clean_cypher(self, raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        return cleaned.strip("`").strip()

    def _execute_cypher(self, cypher: str) -> tuple[list[dict], str | None]:
        try:
            with self.neo4j_driver.session() as session:
                result = session.run(cypher)
                return [dict(r) for r in result], None
        except Exception as e:
            return [], str(e)

    def _search_chroma(self, query: str, n_results: int = 5,
                       filter_ids: list[str] | None = None) -> list[dict]:
        """Search Chroma for similar patient profiles."""
        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
        }
        if filter_ids:
            kwargs["where"] = {"$or": [{"patient_id": pid} for pid in filter_ids[:50]]}

        try:
            results = self.collection.query(**kwargs)
        except Exception:
            # If filter fails (e.g. too many IDs), search without filter
            kwargs.pop("where", None)
            results = self.collection.query(**kwargs)

        matches = []
        for i in range(len(results["ids"][0])):
            matches.append({
                "patient_id": results["ids"][0][i],
                "summary": results["documents"][0][i],
                "distance": results["distances"][0][i],
            })
        return matches

    def generate_cypher(self, question: str) -> str:
        raw = self.cypher_chain.invoke({
            "schema": GRAPH_SCHEMA,
            "question": question,
            "few_shots": format_few_shots(),
        })
        return self._clean_cypher(raw)

    # ------------------------------------------------------------------
    # Structured query (Neo4j)
    # ------------------------------------------------------------------
    def _query_structured(self, question: str) -> dict:
        result = {
            "question": question,
            "route": "structured",
            "cypher": None,
            "results": [],
            "answer": None,
            "confidence": None,
            "citations": None,
            "error": None,
            "retries": 0,
        }

        cypher = self.generate_cypher(question)
        result["cypher"] = cypher
        cypher_generated = bool(cypher and len(cypher) > 5)

        records, error = self._execute_cypher(cypher)

        if error and result["retries"] < MAX_CYPHER_RETRIES:
            retry_q = (
                f"{question}\n\nThe previous query failed: {error}\n"
                f"Failed query: {cypher}\nGenerate a corrected Cypher query."
            )
            for _ in range(MAX_CYPHER_RETRIES):
                result["retries"] += 1
                cypher = self.generate_cypher(retry_q)
                result["cypher"] = cypher
                records, error = self._execute_cypher(cypher)
                if not error:
                    break

        result["results"] = records
        result["error"] = error

        confidence = compute_confidence(
            cypher_generated=cypher_generated,
            execution_error=error,
            result_count=len(records),
            retries=result["retries"],
        )
        result["confidence"] = confidence

        if confidence.score < CONFIDENCE_LOW:
            reason = f"Query error: {error}" if error else "No results returned."
            result["answer"] = REFUSAL_TEMPLATE.format(reason=reason)
            return result

        display_results = records[:25]
        results_str = str(display_results) if display_results else "(no results)"

        answer = self.answer_chain.invoke({
            "question": question,
            "cypher": cypher,
            "results": results_str,
            "result_count": len(records),
            "confidence_label": confidence.label,
        })

        if confidence.label == "moderate":
            answer += CAVEAT_SUFFIX

        result["answer"] = answer

        valid_ids = extract_result_ids(records)
        result["citations"] = validate_citations(answer, valid_ids)

        return result

    # ------------------------------------------------------------------
    # Semantic query (Chroma)
    # ------------------------------------------------------------------
    def _query_semantic(self, question: str) -> dict:
        result = {
            "question": question,
            "route": "semantic",
            "cypher": None,
            "results": [],
            "answer": None,
            "confidence": None,
            "citations": None,
            "error": None,
            "retries": 0,
        }

        matches = self._search_chroma(question, n_results=5)
        result["results"] = matches

        confidence = compute_confidence(
            cypher_generated=True,   # no Cypher but search succeeded
            execution_error=None,
            result_count=len(matches),
            retries=0,
        )
        result["confidence"] = confidence

        if not matches:
            result["answer"] = REFUSAL_TEMPLATE.format(reason="No similar patients found.")
            return result

        results_str = ""
        for i, m in enumerate(matches, 1):
            results_str += f"\n[{i}] Patient {m['patient_id']} (distance: {m['distance']:.3f}):\n"
            results_str += f"    {m['summary'][:300]}...\n"

        answer = self.semantic_chain.invoke({
            "question": question,
            "results": results_str,
            "result_count": len(matches),
        })

        if confidence.label == "moderate":
            answer += CAVEAT_SUFFIX

        result["answer"] = answer

        valid_ids = {m["patient_id"] for m in matches}
        # Also add partial IDs
        for pid in list(valid_ids):
            if len(pid) >= 8:
                valid_ids.add(pid[:8])
        result["citations"] = validate_citations(answer, valid_ids)

        return result

    # ------------------------------------------------------------------
    # Hybrid query (Neo4j filter + Chroma ranking)
    # ------------------------------------------------------------------
    def _query_hybrid(self, question: str) -> dict:
        result = {
            "question": question,
            "route": "hybrid",
            "cypher": None,
            "results": [],
            "answer": None,
            "confidence": None,
            "citations": None,
            "error": None,
            "retries": 0,
        }

        # Step 1: Structured query to get candidate patients
        cypher = self.generate_cypher(question)
        result["cypher"] = cypher
        records, error = self._execute_cypher(cypher)

        if error:
            # Fall back to pure semantic
            return self._query_semantic(question)

        # Step 2: Semantic search (unfiltered — Chroma where filter is fragile)
        matches = self._search_chroma(question, n_results=5)

        result["results"] = {"structured": records, "semantic": matches}

        # Confidence from both
        confidence = compute_confidence(
            cypher_generated=True,
            execution_error=error,
            result_count=len(records) + len(matches),
            retries=0,
        )
        result["confidence"] = confidence

        # Format for LLM
        structured_str = str(records[:15]) if records else "(no structured results)"
        semantic_str = ""
        for i, m in enumerate(matches, 1):
            semantic_str += f"\n[{i}] Patient {m['patient_id']} (dist: {m['distance']:.3f}):\n"
            semantic_str += f"    {m['summary'][:200]}...\n"

        answer = self.hybrid_chain.invoke({
            "question": question,
            "structured_results": structured_str,
            "structured_count": len(records),
            "semantic_results": semantic_str,
            "semantic_count": len(matches),
        })

        if confidence.label == "moderate":
            answer += CAVEAT_SUFFIX

        result["answer"] = answer

        # Validate citations from both sources
        valid_ids = extract_result_ids(records)
        for m in matches:
            valid_ids.add(m["patient_id"])
            if len(m["patient_id"]) >= 8:
                valid_ids.add(m["patient_id"][:8])
        result["citations"] = validate_citations(answer, valid_ids)

        return result

    # ------------------------------------------------------------------
    # Main query dispatcher
    # ------------------------------------------------------------------
    def query(self, question: str) -> dict:
        """Route question to the appropriate retrieval path."""
        route = classify_query(question)

        if route.route_type == "off_topic":
            return {
                "question": question,
                "route": "off_topic",
                "cypher": None,
                "results": [],
                "answer": OFFTOPIC_RESPONSE,
                "confidence": ConfidenceScore(0, "refused", {"reason": route.reason}),
                "citations": None,
                "error": None,
                "retries": 0,
            }

        if route.route_type == "semantic":
            return self._query_semantic(question)
        elif route.route_type == "hybrid":
            return self._query_hybrid(question)
        else:
            return self._query_structured(question)

    def close(self):
        self.neo4j_driver.close()


# ---------------------------------------------------------------------------
# Test queries — covers all three routes
# ---------------------------------------------------------------------------
TEST_QUERIES = [
    # Structured
    ("How many patients have diabetes?", "structured"),
    ("List 5 patients with heart failure and their ages", "structured"),
    ("Show me patients prescribed both warfarin and aspirin", "structured"),
    ("How many inpatient encounters are in the database?", "structured"),

    # Semantic
    ("Find patients similar to elderly cardiac patients", "semantic"),
    ("Describe patients with complex medical histories", "semantic"),

    # Hybrid
    ("Find diabetic patients with complex medication histories", "hybrid"),
    ("High-risk patients with heart failure and multiple medications", "hybrid"),

    # Off-topic
    ("What is the weather today?", "off_topic"),
]


def run_tests(agent: MediQueryAgent):
    print("=== MediQuery GraphRAG Agent v3 — Hybrid Retrieval Test ===\n")

    for i, (question, expected_route) in enumerate(TEST_QUERIES, 1):
        print(f"--- Query {i}/{len(TEST_QUERIES)} ---")
        print(f"  Q: {question}")
        print(f"  Expected route: {expected_route}")

        result = agent.query(question)
        actual_route = result["route"]
        route_match = actual_route == expected_route

        print(f"  Actual route: {actual_route} {'PASS' if route_match else 'MISMATCH'}")

        if result["cypher"]:
            print(f"  Cypher: {result['cypher']}")

        conf = result["confidence"]
        print(f"  Confidence: {conf.score}/100 ({conf.label})")

        if result["error"]:
            print(f"  ERROR: {result['error']}")

        # Truncate long answers for display
        answer = result["answer"]
        if len(answer) > 300:
            answer = answer[:300] + "..."
        print(f"  Answer: {answer}")

        if result["citations"]:
            c = result["citations"]
            cited = len(c["cited_ids"])
            valid = len(c["valid_citations"])
            hallucinated = len(c["hallucinated_citations"])
            print(f"  Citations: {cited} cited, {valid} valid, {hallucinated} hallucinated")

        print()

    print("=== Test complete ===")


def interactive_mode(agent: MediQueryAgent):
    print("=== MediQuery GraphRAG Agent v3 — Hybrid Retrieval ===")
    print("    Routes: structured (Neo4j) | semantic (Chroma) | hybrid (both)")
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

        print(f"\n  Route: {result['route']}")
        if result["cypher"]:
            print(f"  Cypher: {result['cypher']}")
        print(f"  Confidence: {conf.score}/100 ({conf.label})")

        if result["error"]:
            print(f"  Error: {result['error']}")

        print(f"\n  {result['answer']}")

        if result["citations"]:
            c = result["citations"]
            if c["hallucinated_citations"]:
                print(f"\n  WARNING: {len(c['hallucinated_citations'])} hallucinated citation(s)")

        print()


def main():
    print("Initializing MediQuery GraphRAG Agent v3...")
    print(f"  LLM: {OLLAMA_MODEL} via Ollama")
    print(f"  Neo4j: {NEO4J_URI}")
    print(f"  Chroma: {CHROMA_PATH}")
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

    print(f"  Chroma: {agent.collection.count():,} documents loaded")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_tests(agent)
    else:
        interactive_mode(agent)

    agent.close()
    print("Done.")


if __name__ == "__main__":
    main()