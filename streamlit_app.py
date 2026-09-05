"""
MediQuery Streamlit Application — 4-persona clinical data UI.

Connects to FastAPI backend (localhost:8080) for RBAC-enforced queries.

Roles:
  Doctor     — full chat + cohort builder + anomaly alerts
  Researcher — de-identified chat + cohort builder
  Admin      — operational metrics only
  Patient    — own health record

Usage:
    # Terminal 1: API
    uvicorn app.main:app --port 8080

    # Terminal 2: UI
    streamlit run streamlit_app.py
"""

import streamlit as st
import requests
import json

API_BASE = "http://localhost:8080"

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "token" not in st.session_state:
    st.session_state.token = None
if "role" not in st.session_state:
    st.session_state.role = None
if "username" not in st.session_state:
    st.session_state.username = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def api_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}


def api_health():
    try:
        r = requests.get(f"{API_BASE}/health", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def api_login(username: str):
    try:
        r = requests.post(f"{API_BASE}/auth/token", json={"username": username}, timeout=5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def api_query(question: str):
    try:
        r = requests.post(
            f"{API_BASE}/query",
            json={"question": question},
            headers=api_headers(),
            timeout=300
        )
        if r.status_code == 200:
            return r.json()
        return {"error": f"API returned {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def api_cohort(definition: str):
    try:
        r = requests.post(
            f"{API_BASE}/cohort",
            json={"definition": definition},
            headers=api_headers(),
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403:
            return {"error": "Access denied for your role."}
        return {"error": f"API returned {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def api_anomalies():
    try:
        r = requests.get(
            f"{API_BASE}/anomalies/detect",
            headers=api_headers(),
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403:
            return {"error": "Access denied for your role."}
        return {"error": f"API returned {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------
def render_login():
    st.title("MediQuery")
    st.subheader("Clinical Data Intelligence Platform")

    st.markdown("""
    Select a demo persona to explore the platform. Each role sees different
    data based on RBAC enforcement at the API layer.
    """)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("**Doctor**")
        st.caption("Full access to patient data, cohort builder, and anomaly alerts.")
        if st.button("Login as Doctor", use_container_width=True):
            do_login("dr_smith")

    with col2:
        st.markdown("**Researcher**")
        st.caption("De-identified data. Hashed IDs, no patient names.")
        if st.button("Login as Researcher", use_container_width=True):
            do_login("researcher1")

    with col3:
        st.markdown("**Admin**")
        st.caption("Operational metrics only. No patient-level data.")
        if st.button("Login as Admin", use_container_width=True):
            do_login("admin1")

    with col4:
        st.markdown("**Patient**")
        st.caption("View your own health record only.")
        if st.button("Login as Patient", use_container_width=True):
            do_login("patient_demo")

    # Health check
    health = api_health()
    if health:
        status = health.get("status", "unknown")
        color = "green" if status == "healthy" else "red"
        st.markdown(f"API Status: :{color}[{status}]")
    else:
        st.error("Cannot connect to API. Is uvicorn running on port 8080?")


def do_login(username: str):
    result = api_login(username)
    if result:
        st.session_state.token = result["access_token"]
        st.session_state.role = result["role"]
        st.session_state.username = result["username"]
        st.session_state.chat_history = []
        st.rerun()
    else:
        st.error("Login failed. Check if the API is running.")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown(f"### {st.session_state.role.title()} View")
        st.caption(f"Logged in as: {st.session_state.username}")

        role = st.session_state.role

        st.markdown("---")
        st.markdown("**Capabilities:**")
        capabilities = {
            "doctor": ["Clinical queries", "Patient lookup", "Cohort builder", "Anomaly detection"],
            "researcher": ["De-identified queries", "Cohort builder", "Anomaly detection"],
            "admin": ["Operational metrics", "Provider volume"],
            "patient": ["My health record"],
        }
        for cap in capabilities.get(role, []):
            st.markdown(f"- {cap}")

        st.markdown("---")

        # Example queries by role
        st.markdown("**Try these:**")
        examples = get_example_queries(role)
        for ex in examples:
            if st.button(ex, key=f"ex_{ex[:20]}", use_container_width=True):
                st.session_state.pending_query = ex
                st.rerun()

        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            st.session_state.token = None
            st.session_state.role = None
            st.session_state.username = None
            st.session_state.chat_history = []
            st.rerun()


def get_example_queries(role: str) -> list:
    if role == "doctor":
        return [
            "How many patients have diabetes?",
            "List 5 patients with heart failure",
            "Show patients prescribed warfarin and aspirin",
            "What is the most common condition?",
        ]
    elif role == "researcher":
        return [
            "How many patients have hypertension?",
            "Gender breakdown of diabetic patients",
            "What medications are prescribed to COPD patients?",
        ]
    elif role == "admin":
        return [
            "How many encounters are in the database?",
            "How many providers are there?",
            "What is the most common encounter type?",
        ]
    elif role == "patient":
        return [
            "What conditions do I have?",
            "What medications am I on?",
            "Show my encounter history",
        ]
    return []


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------
def render_chat():
    st.title("Clinical Query")

    # Display chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("meta"):
                with st.expander("Query details"):
                    st.json(msg["meta"])

    # Handle pending query from sidebar buttons
    pending = st.session_state.pop("pending_query", None)

    # Chat input
    question = st.chat_input("Ask a question about the clinical data...")
    if pending:
        question = pending

    if question:
        # Add user message
        st.session_state.chat_history.append({"role": "user", "content": question})

        with st.chat_message("user"):
            st.markdown(question)

        # Get response
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                result = api_query(question)

            if "error" in result:
                st.error(result["error"])
                return

            answer = result.get("answer", "No answer generated.")
            st.markdown(answer)

            # Show metadata in expander
            meta = {
                "route": result.get("route"),
                "confidence": f"{result.get('confidence_score', '?')}/100 ({result.get('confidence_label', '?')})",
                "result_count": result.get("result_count", 0),
                "role_applied": result.get("role_applied"),
            }
            if result.get("cypher"):
                meta["cypher"] = result["cypher"]
            if result.get("citations"):
                meta["citations"] = result["citations"]

            with st.expander("Query details"):
                st.json(meta)

        # Save to history
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": answer,
            "meta": meta,
        })


# ---------------------------------------------------------------------------
# Cohort builder (doctor + researcher)
# ---------------------------------------------------------------------------
def render_cohort_builder():
    st.title("Cohort Builder")
    st.caption("Define a patient population in natural language.")

    definition = st.text_input(
        "Cohort definition",
        placeholder="e.g., Diabetic patients over 65 with inpatient encounters",
    )

    if st.button("Build Cohort", disabled=not definition):
        with st.spinner("Building cohort... (this may take a minute)"):
            result = api_cohort(definition)

        if "error" in result and result["error"]:
            st.error(result["error"])
            return

        # Summary
        st.success(f"Found {result.get('patient_count', 0)} patients")

        col1, col2 = st.columns(2)

        # Demographics
        with col1:
            st.subheader("Demographics")
            demo = result.get("demographics", {})

            if demo.get("gender"):
                st.markdown("**Gender:**")
                for g in demo["gender"]:
                    st.markdown(f"- {g.get('gender', '?')}: {g.get('count', 0)}")

            age = demo.get("age_stats", {})
            if age:
                st.markdown(f"**Age:** {age.get('min_age', '?')} - {age.get('max_age', '?')} "
                          f"(avg {age.get('avg_age', '?'):.1f})" if isinstance(age.get('avg_age'), (int, float)) else "")

            if demo.get("race"):
                st.markdown("**Race:**")
                for r in demo["race"][:5]:
                    st.markdown(f"- {r.get('race', '?')}: {r.get('count', 0)}")

        # Conditions
        with col2:
            st.subheader("Top Conditions")
            conditions = result.get("top_conditions", [])
            for c in conditions[:8]:
                if c.get("chronic_condition"):
                    st.markdown(f"- **{c['chronic_condition']}**: {c.get('patient_count', '?')}")
                elif c.get("condition") and c["condition"] != "---top disorders---":
                    st.markdown(f"- {c['condition']}: {c.get('patient_count', '?')}")

        # Medications
        st.subheader("Top Medications")
        meds = result.get("top_medications", [])
        if meds:
            med_data = []
            for m in meds[:10]:
                med_data.append({
                    "Medication": m.get("medication", "?")[:50],
                    "Class": m.get("drug_class", "?"),
                    "Patients": m.get("patient_count", 0),
                })
            st.table(med_data)

        # Encounters
        st.subheader("Encounter Summary")
        encounters = result.get("encounter_summary", [])
        if encounters:
            enc_data = []
            for e in encounters:
                enc_data.append({
                    "Type": e.get("encounter_type", "?"),
                    "Encounters": e.get("encounter_count", 0),
                    "Patients": e.get("patients_with_type", 0),
                })
            st.table(enc_data)

        # Cypher
        with st.expander("Generated Cypher"):
            st.code(result.get("cypher", "N/A"), language="cypher")


# ---------------------------------------------------------------------------
# Anomaly detection (doctor + researcher)
# ---------------------------------------------------------------------------
def render_anomalies():
    st.title("Anomaly Detection")
    st.caption("Drug interaction and readmission alerts.")

    if st.button("Run Anomaly Detection"):
        with st.spinner("Detecting anomalies... (this may take a minute)"):
            result = api_anomalies()

        if "error" in result:
            st.error(result["error"])
            return

        anomalies = result.get("anomalies", {})

        for atype, info in anomalies.items():
            severity = info.get("severity", "unknown")
            color = "red" if severity == "high" else "orange"

            st.markdown(f"### :{color}[{atype.replace('_', ' ').title()}]")
            st.markdown(f"**{info.get('description', '')}**")
            st.metric("Patients flagged", info.get("patient_count", 0))

            patients = info.get("patients", [])
            if patients:
                with st.expander(f"View flagged patients ({len(patients)} shown)"):
                    st.json(patients[:5])

            st.markdown("---")


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
def render_admin_dashboard():
    st.title("Operational Dashboard")
    st.caption("System-level metrics. No patient-level data.")

    queries = [
        ("Total Patients", "How many patients are in the database?"),
        ("Total Encounters", "How many encounters are in the database?"),
        ("Total Providers", "How many providers are in the database?"),
        ("Inpatient Encounters", "How many inpatient encounters are in the database?"),
    ]

    cols = st.columns(len(queries))

    for col, (label, question) in zip(cols, queries):
        with col:
            with st.spinner(label):
                result = api_query(question)
                answer = result.get("answer", "N/A")
                # Try to extract the number from the answer
                import re
                numbers = re.findall(r'[\d,]+', answer)
                if numbers:
                    st.metric(label, numbers[0])
                else:
                    st.metric(label, "?")
                    st.caption(answer[:50])


# ---------------------------------------------------------------------------
# Patient view
# ---------------------------------------------------------------------------
def render_patient_view():
    st.title("My Health Record")
    st.caption("Your personal health data.")

    tabs = st.tabs(["Conditions", "Medications", "Encounters"])

    with tabs[0]:
        with st.spinner("Loading conditions..."):
            result = api_query("What conditions do I have?")
        st.markdown(result.get("answer", "No data available."))

    with tabs[1]:
        with st.spinner("Loading medications..."):
            result = api_query("What medications am I on?")
        st.markdown(result.get("answer", "No data available."))

    with tabs[2]:
        with st.spinner("Loading encounters..."):
            result = api_query("Show my encounter history")
        st.markdown(result.get("answer", "No data available."))


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="MediQuery",
        page_icon="🏥",
        layout="wide",
    )

    # Not logged in → show login
    if not st.session_state.token:
        render_login()
        return

    # Logged in → show role-specific UI
    render_sidebar()

    role = st.session_state.role

    if role == "admin":
        render_admin_dashboard()
        return

    if role == "patient":
        render_patient_view()
        return

    # Doctor and Researcher: tabbed interface
    tabs = st.tabs(["Clinical Query", "Cohort Builder", "Anomaly Detection"])

    with tabs[0]:
        render_chat()

    with tabs[1]:
        render_cohort_builder()

    with tabs[2]:
        render_anomalies()


if __name__ == "__main__":
    main()