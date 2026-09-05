"""
Day 37: RBAC test script.

Starts the API is assumed to be running at localhost:8080.
Tests all endpoints with all four roles.

Usage:
    # Terminal 1: start the API
    uvicorn app.main:app --port 8080

    # Terminal 2: run tests
    python app/test_rbac.py
"""

import requests
import json
import sys

BASE = "http://localhost:8080"

DEMO_USERS = ["dr_smith", "researcher1", "admin1", "patient_demo"]


def get_token(username: str) -> str:
    """Get a JWT for a demo user."""
    r = requests.post(f"{BASE}/auth/token", json={"username": username})
    if r.status_code != 200:
        print(f"  AUTH FAILED for {username}: {r.status_code} {r.text}")
        return None
    data = r.json()
    print(f"  {username} → role={data['role']}, token={data['access_token'][:20]}...")
    return data["access_token"]


def test_query(token: str, role: str, question: str):
    """Test the /query endpoint."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{BASE}/query", json={"question": question}, headers=headers)

    if r.status_code == 403:
        print(f"    /query: BLOCKED (403) — correct if role shouldn't access")
        return

    if r.status_code != 200:
        print(f"    /query: ERROR {r.status_code}")
        return

    data = r.json()
    answer = data.get("answer", "")[:100]
    print(f"    /query: {data.get('confidence_label', '?')} confidence, "
          f"role={data.get('role_applied')}")
    print(f"            answer: {answer}...")

    # Check role-specific filtering
    if role == "researcher":
        if data.get("cypher"):
            print(f"            cypher visible: YES (expected for researcher)")
        if data.get("citations"):
            print(f"            WARNING: citations visible to researcher")
    elif role == "admin":
        if "restricted" in answer.lower() or "aggregate" in answer.lower():
            print(f"            admin filtering: ACTIVE")
        if data.get("cypher"):
            print(f"            WARNING: cypher visible to admin")
    elif role == "patient":
        pass  # patient data filtering tested separately


def test_cohort(token: str, role: str, definition: str):
    """Test the /cohort endpoint."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{BASE}/cohort", json={"definition": definition}, headers=headers)

    if r.status_code == 403:
        print(f"    /cohort: BLOCKED (403) — {'CORRECT' if role in ('admin', 'patient') else 'WRONG'}")
        return

    if r.status_code != 200:
        print(f"    /cohort: ERROR {r.status_code} {r.text[:100]}")
        return

    data = r.json()
    print(f"    /cohort: {data.get('patient_count', 0)} patients, role={data.get('role_applied')}")


def test_anomalies(token: str, role: str):
    """Test the /anomalies/detect endpoint."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BASE}/anomalies/detect", headers=headers)

    if r.status_code == 403:
        print(f"    /anomalies: BLOCKED (403) — {'CORRECT' if role in ('admin', 'patient') else 'WRONG'}")
        return

    if r.status_code != 200:
        print(f"    /anomalies: ERROR {r.status_code}")
        return

    data = r.json()
    for atype, info in data.get("anomalies", {}).items():
        print(f"    /anomalies: {atype} → {info.get('patient_count', 0)} patients")


def test_no_auth():
    """Test endpoints without a token."""
    print("\n--- No Auth ---")
    r = requests.post(f"{BASE}/query", json={"question": "How many patients?"})
    print(f"  /query without token: {r.status_code} "
          f"({'BLOCKED' if r.status_code in (401, 403) else 'EXPOSED'})")

    r = requests.post(f"{BASE}/cohort", json={"definition": "all patients"})
    print(f"  /cohort without token: {r.status_code} "
          f"({'BLOCKED' if r.status_code in (401, 403) else 'EXPOSED'})")

    r = requests.get(f"{BASE}/anomalies/detect")
    print(f"  /anomalies without token: {r.status_code} "
          f"({'BLOCKED' if r.status_code in (401, 403) else 'EXPOSED'})")


def main():
    print("Day 37: RBAC Test Suite")
    print(f"API: {BASE}\n")

    # Health check
    try:
        r = requests.get(f"{BASE}/health")
        if r.status_code == 200:
            data = r.json()
            print(f"Health: {data.get('status')}")
            for svc, status in data.get("services", {}).items():
                print(f"  {svc}: {status}")
        else:
            print(f"Health check failed: {r.status_code}")
    except requests.ConnectionError:
        print("ERROR: Cannot connect to API. Is it running?")
        print("Start it with: uvicorn app.main:app --port 8080")
        sys.exit(1)

    # Get tokens
    print("\n--- Authentication ---")
    tokens = {}
    for user in DEMO_USERS:
        token = get_token(user)
        if token:
            tokens[user] = token

    if not tokens:
        print("No tokens obtained. Exiting.")
        sys.exit(1)

    # Test each role
    question = "List 5 patients with heart failure and their ages"
    cohort_def = "Diabetic patients over 65"

    role_map = {
        "dr_smith": "doctor",
        "researcher1": "researcher",
        "admin1": "admin",
        "patient_demo": "patient",
    }

    for user, token in tokens.items():
        role = role_map[user]
        print(f"\n--- Role: {role} ({user}) ---")

        print(f"  Testing /query:")
        test_query(token, role, question)

        print(f"  Testing /cohort:")
        test_cohort(token, role, cohort_def)

        print(f"  Testing /anomalies:")
        test_anomalies(token, role)

    # Test without auth
    test_no_auth()

    print("\n--- Summary ---")
    print("  Expected access matrix:")
    print("  Endpoint    | Doctor | Researcher | Admin | Patient")
    print("  /query      |   ✓    |     ✓      |   ✓   |    ✓   ")
    print("  /cohort     |   ✓    |     ✓      |   ✗   |    ✗   ")
    print("  /anomalies  |   ✓    |     ✓      |   ✗   |    ✗   ")
    print("  No auth     |   ✗    |     ✗      |   ✗   |    ✗   ")

    print("\nDone.")


if __name__ == "__main__":
    main()