import json, glob, os

patient_id = "bca84d73-f1a6-c867-56b6-c55f493f1ead"
med_id = "bca84d73-f1a6-c867-0956-686061992c0a"

# Synthea filenames embed the patient ID. Find it.
search_dirs = [
    r"synthea\output\fhir",
    r"D:\Projects\mediquery\synthea\output\fhir",
]

target_file = None
for d in search_dirs:
    if not os.path.isdir(d):
        continue
    matches = glob.glob(os.path.join(d, f"*{patient_id}*.json"))
    if matches:
        target_file = matches[0]
        break

if not target_file:
    print(f"Could not find bundle for patient {patient_id}")
    print(f"Checked: {search_dirs}")
    print("List files manually: dir synthea\\output\\fhir\\*.json | findstr bca84d73")
else:
    print(f"Found: {target_file}")
    with open(target_file, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    found = False
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if res.get("resourceType") == "MedicationRequest" and res.get("id") == med_id:
            found = True
            print("=" * 70)
            print("MEDICATIONREQUEST RESOURCE:")
            print("=" * 70)
            print(json.dumps(res, indent=2))
            print("=" * 70)
            print("KEY FIELDS:")
            print(f"  medicationCodeableConcept present: {'medicationCodeableConcept' in res}")
            print(f"  medicationReference present:       {'medicationReference' in res}")
            print(f"  medication present (R5 style):     {'medication' in res}")
            print(f"  contained resources:               {len(res.get('contained', []))}")
            break

    if not found:
        print(f"MedicationRequest {med_id} not found in this bundle.")