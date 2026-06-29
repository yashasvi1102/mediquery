import json, glob, os

patient_id = "bca84d73-f1a6-c867-56b6-c55f493f1ead"
med_uuid = "7f9db6ff-8430-5a4e-064d-1f08cf029629"

target_file = glob.glob(rf"synthea\output\fhir\*{patient_id}*.json")[0]

with open(target_file, "r", encoding="utf-8") as f:
    bundle = json.load(f)

# Count resource types in this bundle
counts = {}
medication_resources = []
for entry in bundle.get("entry", []):
    res = entry.get("resource", {})
    rt = res.get("resourceType", "Unknown")
    counts[rt] = counts.get(rt, 0) + 1
    if rt == "Medication":
        medication_resources.append({
            "fullUrl": entry.get("fullUrl"),
            "id": res.get("id"),
            "code": res.get("code"),
        })

print("Resource type counts in this bundle:")
for rt, n in sorted(counts.items(), key=lambda x: -x[1]):
    print(f"  {rt}: {n}")

print()
print(f"Medication resources in bundle: {len(medication_resources)}")
print()

# Find the one referenced by the broken MedicationRequest
print(f"Looking for fullUrl = urn:uuid:{med_uuid}")
target = next((m for m in medication_resources if m["fullUrl"] == f"urn:uuid:{med_uuid}"), None)
if target:
    print("FOUND IT:")
    print(json.dumps(target, indent=2))
else:
    print("NOT FOUND in this bundle. Check first 2 Medication resources:")
    for m in medication_resources[:2]:
        print(json.dumps(m, indent=2))