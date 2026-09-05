import requests, json
BASE = "http://localhost:8080"

# Get tokens
dr = requests.post(f"{BASE}/auth/token", json={"username": "dr_smith"}).json()["access_token"]
res = requests.post(f"{BASE}/auth/token", json={"username": "researcher1"}).json()["access_token"]
pat = requests.post(f"{BASE}/auth/token", json={"username": "patient_demo"}).json()["access_token"]

q = {"question": "List 3 patients with diabetes and their ages"}

print("=== DOCTOR ===")
r = requests.post(f"{BASE}/query", json=q, headers={"Authorization": f"Bearer {dr}"}).json()
print(r["answer"][:300])

print("\n=== RESEARCHER ===")
r = requests.post(f"{BASE}/query", json=q, headers={"Authorization": f"Bearer {res}"}).json()
print(r["answer"][:300])

print("\n=== PATIENT ===")
r = requests.post(f"{BASE}/query", json=q, headers={"Authorization": f"Bearer {pat}"}).json()
print(r["answer"][:300])
