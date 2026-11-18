import requests
BASE = "http://localhost:8080"

# Login as patient
r = requests.post(f"{BASE}/auth/token", json={"username": "patient_demo"})
token = r.json()["access_token"]
pid = "5764ecd4-4c5f-fc7c-a979-e3cb405b8f10"
print(f"Token patient_id check:")

# Login as doctor and query for this patient
r2 = requests.post(f"{BASE}/auth/token", json={"username": "dr_smith"})
doc_token = r2.json()["access_token"]

# Doctor queries this specific patient
q = f"What conditions does patient {pid} have?"
r3 = requests.post(f"{BASE}/query", json={"question": q},
                   headers={"Authorization": f"Bearer {doc_token}"}, timeout=300)
print(f"\nDoctor query: {q}")
print(f"Answer: {r3.json().get('answer','')[:300]}")

# Patient queries own conditions  
r4 = requests.post(f"{BASE}/query", json={"question": "What conditions do I have?"},
                   headers={"Authorization": f"Bearer {token}"}, timeout=300)
print(f"\nPatient query: What conditions do I have?")
print(f"Answer: {r4.json().get('answer','')[:300]}")
print(f"Result count: {r4.json().get('result_count')}")
print(f"Route: {r4.json().get('route')}")
