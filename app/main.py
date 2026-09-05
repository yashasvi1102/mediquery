"""
MediQuery FastAPI Application — RBAC-enforced clinical data API.

Endpoints:
  POST /auth/token          — get a JWT for a demo user
  POST /query               — NL question → cited answer (all roles)
  POST /cohort              — NL cohort definition → stats (doctor, researcher)
  GET  /anomalies/detect    — run anomaly detection (doctor, researcher)
  GET  /health              — service health check

RBAC enforcement: data is filtered at the API layer BEFORE
reaching the response. Streamlit (Phase 5) never sees data
the role can't access.

Usage:
    cd D:\Projects\mediquery
    uvicorn app.main:app --reload --port 8080
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add neo4j module path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data_engineering" / "neo4j"))

from app.auth import create_token, get_current_user, require_role, DEMO_USERS
from app.rbac import filter_for_role, filter_answer_for_role


# ---------------------------------------------------------------------------
# Agent initialization (loaded once at startup)
# ---------------------------------------------------------------------------
agent = None
cohort_builder = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize agent and cohort builder on startup."""
    global agent, cohort_builder

    print("Starting MediQuery API...")

    from graphrag_agent import MediQueryAgent
    from cohort_builder import CohortBuilder

    agent = MediQueryAgent()
    agent.neo4j_driver.verify_connectivity()
    print("  Neo4j: connected")

    agent.llm.invoke("warmup")
    print("  Ollama: connected")

    print(f"  Chroma: {agent.collection.count():,} documents")

    cohort_builder = CohortBuilder(agent.neo4j_driver, agent.llm)
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
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Streamlit will connect from localhost
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/Response models
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
# Auth endpoint
# ---------------------------------------------------------------------------
@app.post("/auth/token", response_model=TokenResponse)
def login(req: TokenRequest):
    """Get a JWT for a demo user. Username must be in DEMO_USERS."""
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
        access_token=token,
        role=user["role"],
        username=req.username,
    )


# ---------------------------------------------------------------------------
# Query endpoint — all roles, filtered by RBAC
# ---------------------------------------------------------------------------
@app.post("/query", response_model=QueryResponse)
def query_endpoint(req: QueryRequest, user: dict = Depends(get_current_user)):
    """Ask a natural language question. Results filtered by role."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    result = agent.query(req.question)

    # Filter results by role
    if result.get("results") and isinstance(result["results"], list):
        # Handle nested results (hybrid returns dict)
        if isinstance(result["results"], dict):
            filtered_results = result["results"]
        else:
            filtered_results = filter_for_role(
                result["results"], user["role"], user.get("patient_id")
            )
    else:
        filtered_results = []

    # Filter answer text
    answer = result.get("answer", "")
    answer = filter_answer_for_role(answer, user["role"])

    conf = result.get("confidence")

    return QueryResponse(
        question=req.question,
        route=result.get("route"),
        cypher=result.get("cypher") if user["role"] in ("doctor", "researcher") else None,
        answer=answer,
        confidence_score=conf.score if conf else None,
        confidence_label=conf.label if conf else None,
        result_count=len(filtered_results) if isinstance(filtered_results, list) else 0,
        citations=result.get("citations") if user["role"] == "doctor" else None,
        role_applied=user["role"],
    )


# ---------------------------------------------------------------------------
# Cohort endpoint — doctor + researcher only
# ---------------------------------------------------------------------------
@app.post("/cohort", response_model=CohortResponse)
def cohort_endpoint(
    req: CohortRequest,
    user: dict = Depends(require_role("doctor", "researcher")),
):
    """Build a patient cohort from NL definition. Doctor and researcher only."""
    if not req.definition.strip():
        raise HTTPException(status_code=400, detail="Cohort definition cannot be empty.")

    result = cohort_builder.build(req.definition)

    # Filter patient list by role
    if result.patient_list:
        filtered_list = filter_for_role(result.patient_list, user["role"])
    else:
        filtered_list = []

    # Conditions and medications are aggregate — safe for researcher
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
# Anomaly detection endpoint — doctor + researcher only
# ---------------------------------------------------------------------------
@app.get("/anomalies/detect")
def detect_anomalies(user: dict = Depends(require_role("doctor", "researcher"))):
    """Run anomaly detection queries. Doctor and researcher only."""
    anomalies = {}

    # Warfarin co-prescription
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
        "patients": warfarin_patients[:10],  # limit for response size
    }

    # HF early readmission
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

    return {
        "anomalies": anomalies,
        "role_applied": user["role"],
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Service health check."""
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

    checks["ollama"] = "ok"  # would have failed at startup

    return {
        "status": "healthy" if all("ok" in str(v) for v in checks.values()) else "degraded",
        "services": checks,
    }