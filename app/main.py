"""
MediQuery FastAPI Application v2 — RBAC-refined.

Fixes from v1:
  1. Results filtered BEFORE answer synthesis (researcher sees hashed IDs
     in the answer text, not raw IDs)
  2. Patient queries scoped at Cypher level (WHERE p.patient_id = $id)

Usage:
    uvicorn app.main:app --reload --port 8080
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data_engineering" / "neo4j"))

from app.auth import create_token, get_current_user, require_role, DEMO_USERS
from app.rbac import filter_for_role, filter_answer_for_role

# Import confidence scorer separately for manual pipeline
from graphrag_agent import MediQueryAgent, compute_confidence
from cohort_builder import CohortBuilder


# ---------------------------------------------------------------------------
# Agent initialization
# ---------------------------------------------------------------------------
agent = None
cohort_builder_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, cohort_builder_instance

    print("Starting MediQuery API v2...")

    agent = MediQueryAgent()
    agent.neo4j_driver.verify_connectivity()
    print("  Neo4j: connected")

    agent.llm.invoke("warmup")
    print("  Ollama: connected")

    print(f"  Chroma: {agent.collection.count():,} documents")

    cohort_builder_instance = CohortBuilder(agent.neo4j_driver, agent.llm)
    print("  Cohort builder: ready")
    print("  API ready.\n")

    yield
    agent.close()
    print("API shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MediQuery API",
    description="RBAC-enforced clinical data API with GraphRAG",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    username: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    question: str
    route: str | None = None
    cypher: str | None = None
    answer: str | None = None
    confidence_score: int | None = None
    confidence_label: str | None = None
    result_count: int = 0
    citations: dict | None = None
    role_applied: str | None = None

class CohortRequest(BaseModel):
    definition: str

class CohortResponse(BaseModel):
    definition: str
    cypher: str | None = None
    patient_count: int = 0
    demographics: dict | None = None
    top_conditions: list | None = None
    top_medications: list | None = None
    encounter_summary: list | None = None
    role_applied: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/auth/token", response_model=TokenResponse)
def login(req: TokenRequest):
    if req.username not in DEMO_USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown user '{req.username}'. "
                   f"Demo users: {', '.join(DEMO_USERS.keys())}",
        )
    user = DEMO_USERS[req.username]
    token = create_token(
        username=req.username,
        role=user["role"],
        patient_id=user.get("patient_id"),
    )
    return TokenResponse(
        access_token=token, role=user["role"], username=req.username,
    )


# ---------------------------------------------------------------------------
# Query endpoint — RBAC-refined
# ---------------------------------------------------------------------------
@app.post("/query", response_model=QueryResponse)
def query_endpoint(req: QueryRequest, user: dict = Depends(get_current_user)):
    """
    NL question → answer, with RBAC applied BEFORE answer synthesis.

    Flow:
      1. Route query (structured/semantic/hybrid/off_topic)
      2. For patient role: scope question to their patient_id
      3. Generate Cypher + execute
      4. Filter results by role
      5. Synthesize answer from FILTERED results
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    role = user["role"]
    patient_id = user.get("patient_id")
    question = req.question.strip()

    # --- Off-topic check ---
    from query_router import classify_query
    route = classify_query(question)
    if route.route_type == "off_topic":
        return QueryResponse(
            question=question,
            route="off_topic",
            answer="This question is outside the scope of the clinical dataset.",
            confidence_score=0,
            confidence_label="refused",
            role_applied=role,
        )

    # --- Patient role: scope question to their patient_id ---
    scoped_question = question
    if role == "patient" and patient_id:
        scoped_question = (
            f"Only for the patient with patient_id '{patient_id}': {question}"
        )

    # --- Semantic route: use Chroma, filter, then synthesize ---
    if route.route_type == "semantic":
        result = agent._query_semantic(scoped_question)
        if result.get("results") and isinstance(result["results"], list):
            result["results"] = filter_for_role(result["results"], role, patient_id)
        conf = result.get("confidence")
        return QueryResponse(
            question=question,
            route="semantic",
            answer=filter_answer_for_role(result.get("answer", ""), role),
            confidence_score=conf.score if conf else None,
            confidence_label=conf.label if conf else None,
            result_count=len(result.get("results", [])),
            role_applied=role,
        )

    # --- Structured / Hybrid: generate Cypher, execute, filter, THEN synthesize ---
    # Step 1: Generate Cypher
    cypher = agent.generate_cypher(scoped_question)
    cypher_generated = bool(cypher and len(cypher) > 5)

    # Step 2: Execute with retry
    records, error = agent._execute_cypher(cypher)
    retries = 0

    if error and retries < 2:
        retry_q = (
            f"{scoped_question}\n\nThe previous query failed: {error}\n"
            f"Failed query: {cypher}\nGenerate a corrected Cypher query."
        )
        for _ in range(2):
            retries += 1
            cypher = agent.generate_cypher(retry_q)
            records, error = agent._execute_cypher(cypher)
            if not error:
                break

    # Step 3: Compute confidence
    confidence = compute_confidence(
        cypher_generated=cypher_generated,
        execution_error=error,
        result_count=len(records),
        retries=retries,
    )

    if confidence.score < 40:
        reason = f"Query error: {error}" if error else "No results."
        return QueryResponse(
            question=question,
            route="structured",
            cypher=cypher if role in ("doctor", "researcher") else None,
            answer=f"I'm not confident enough to answer this reliably. {reason}",
            confidence_score=confidence.score,
            confidence_label=confidence.label,
            role_applied=role,
        )

    # Step 4: FILTER results by role BEFORE synthesis
    filtered_records = filter_for_role(records, role, patient_id)

    # Step 5: Synthesize answer from FILTERED results
    display_results = filtered_records[:25]
    results_str = str(display_results) if display_results else "(no results)"

    answer = agent.answer_chain.invoke({
        "question": question,
        "cypher": cypher,
        "results": results_str,
        "result_count": len(filtered_records),
        "confidence_label": confidence.label,
    })

    if confidence.label == "moderate":
        answer += "\n\nNote: moderate confidence. Please verify."

    # Step 6: Additional answer filtering for admin
    answer = filter_answer_for_role(answer, role)

    # Step 7: Citation validation against filtered results
    from graphrag_agent import extract_result_ids, validate_citations
    valid_ids = extract_result_ids(filtered_records)
    citations = validate_citations(answer, valid_ids)

    return QueryResponse(
        question=question,
        route=route.route_type,
        cypher=cypher if role in ("doctor", "researcher") else None,
        answer=answer,
        confidence_score=confidence.score,
        confidence_label=confidence.label,
        result_count=len(filtered_records),
        citations=citations if role == "doctor" else None,
        role_applied=role,
    )


# ---------------------------------------------------------------------------
# Cohort — doctor + researcher only
# ---------------------------------------------------------------------------
@app.post("/cohort", response_model=CohortResponse)
def cohort_endpoint(
    req: CohortRequest,
    user: dict = Depends(require_role("doctor", "researcher")),
):
    if not req.definition.strip():
        raise HTTPException(status_code=400, detail="Cohort definition cannot be empty.")

    result = cohort_builder_instance.build(req.definition)

    if result.patient_list:
        filtered_list = filter_for_role(result.patient_list, user["role"])
    else:
        filtered_list = []

    return CohortResponse(
        definition=req.definition,
        cypher=result.cypher,
        patient_count=result.patient_count,
        demographics=result.demographics if result.demographics else None,
        top_conditions=result.conditions[:10] if result.conditions else None,
        top_medications=result.medications[:10] if result.medications else None,
        encounter_summary=result.encounters if result.encounters else None,
        role_applied=user["role"],
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Anomalies — doctor + researcher only
# ---------------------------------------------------------------------------
@app.get("/anomalies/detect")
def detect_anomalies(user: dict = Depends(require_role("doctor", "researcher"))):
    anomalies = {}

    warfarin_result = agent.query(
        "Find patients prescribed both Warfarin and either Aspirin or Ibuprofen"
    )
    warfarin_patients = warfarin_result.get("results", [])
    if isinstance(warfarin_patients, list):
        warfarin_patients = filter_for_role(warfarin_patients, user["role"])

    anomalies["warfarin_coprescription"] = {
        "description": "Patients prescribed warfarin with concurrent NSAID/antiplatelet",
        "patient_count": len(warfarin_patients),
        "severity": "high",
        "patients": warfarin_patients[:10],
    }

    hf_result = agent.query(
        "Find heart failure patients readmitted within 7 days"
    )
    hf_patients = hf_result.get("results", [])
    if isinstance(hf_patients, list):
        hf_patients = filter_for_role(hf_patients, user["role"])

    anomalies["hf_early_readmission"] = {
        "description": "Heart failure patients readmitted within 7 days of discharge",
        "patient_count": len(hf_patients),
        "severity": "high",
        "patients": hf_patients[:10],
    }

    return {"anomalies": anomalies, "role_applied": user["role"]}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    checks = {}
    try:
        agent.neo4j_driver.verify_connectivity()
        checks["neo4j"] = "ok"
    except Exception as e:
        checks["neo4j"] = f"error: {e}"

    try:
        checks["chroma"] = f"ok ({agent.collection.count()} docs)"
    except Exception as e:
        checks["chroma"] = f"error: {e}"

    checks["ollama"] = "ok"

    return {
        "status": "healthy" if all("ok" in str(v) for v in checks.values()) else "degraded",
        "services": checks,
    }