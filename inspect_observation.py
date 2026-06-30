import json, glob, os

# pick any bundle
target_file = glob.glob(r"synthea\output\fhir\Aaron697_Johns824_*.json")[0]

with open(target_file, "r", encoding="utf-8") as f:
    bundle = json.load(f)

observations = []
for entry in bundle.get("entry", []):
    res = entry.get("resource", {})
    if res.get("resourceType") == "Observation":
        observations.append(res)

print(f"Total observations in this bundle: {len(observations)}")
print()

# Show one of each "value shape" we encounter
shapes_seen = {}
for o in observations:
    if "valueQuantity" in o:
        shape = "valueQuantity"
    elif "valueCodeableConcept" in o:
        shape = "valueCodeableConcept"
    elif "valueString" in o:
        shape = "valueString"
    elif "valueBoolean" in o:
        shape = "valueBoolean"
    elif "component" in o:
        shape = "component"
    elif "valueInteger" in o:
        shape = "valueInteger"
    else:
        shape = "OTHER"
    if shape not in shapes_seen:
        shapes_seen[shape] = o

print(f"Distinct value shapes found: {list(shapes_seen.keys())}")
print()

for shape, sample in shapes_seen.items():
    print("=" * 70)
    print(f"SHAPE: {shape}")
    print("=" * 70)
    print(json.dumps(sample, indent=2))
    print()
