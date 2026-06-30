from data_generation.fhir_parser import load_bundle, iter_resources

b = load_bundle(r"synthea\output\fhir\Aaron697_Johns824_bca84d73-f1a6-c867-56b6-c55f493f1ead.json")

# Find observations with NO recognized value field AND no component array.
mystery = []
for o in iter_resources(b, "Observation"):
    has_quantity = "valueQuantity" in o
    has_concept = "valueCodeableConcept" in o
    has_string = "valueString" in o
    has_component = "component" in o
    if not (has_quantity or has_concept or has_string or has_component):
        mystery.append(o)

print(f"Mystery observations (no recognized value): {len(mystery)}")
print()

# What fields DO they have?
from collections import Counter
all_keys = Counter()
for o in mystery:
    all_keys.update(o.keys())
print("Top fields across mystery observations:")
for k, n in all_keys.most_common(20):
    print(f"  {k}: {n}")
print()

# What LOINC codes do they have?
loincs = Counter()
for o in mystery:
    coding = (o.get("code", {}).get("coding") or [{}])[0]
    loincs[(coding.get("code"), coding.get("display"))] += 1
print("Top 10 LOINC codes among mystery observations:")
for (code, display), n in loincs.most_common(10):
    print(f"  {code:15s}  {(display or '')[:55]:55s}  {n}")
print()

# Print one full mystery observation
print("=" * 70)
print("SAMPLE MYSTERY OBSERVATION (full JSON):")
print("=" * 70)
import json
print(json.dumps(mystery[0], indent=2))
