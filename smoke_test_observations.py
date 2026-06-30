from data_generation.fhir_parser import load_bundle, parse_bundle
from collections import Counter

b = load_bundle(r"synthea\output\fhir\Aaron697_Johns824_bca84d73-f1a6-c867-56b6-c55f493f1ead.json")
parsed = parse_bundle(b)

obs = parsed["observations"]
print(f"Total observation rows: {len(obs)}")
print()

# How many have each value type populated?
print("Value type distribution:")
numeric = sum(1 for o in obs if o["value_numeric"] is not None)
text = sum(1 for o in obs if o["value_text"] is not None)
coded = sum(1 for o in obs if o["value_code"] is not None)
empty = sum(1 for o in obs if o["value_numeric"] is None and o["value_text"] is None and o["value_code"] is None)
print(f"  numeric: {numeric}")
print(f"  text:    {text}")
print(f"  coded:   {coded}")
print(f"  empty:   {empty}")
print()

# Blood pressure split check
bp_systolic = [o for o in obs if o["loinc_code"] == "8480-6"]
bp_diastolic = [o for o in obs if o["loinc_code"] == "8462-4"]
print(f"BP systolic rows:  {len(bp_systolic)}")
print(f"BP diastolic rows: {len(bp_diastolic)}")
print("(should be equal -- BP always emits both)")
print()

# Top 10 LOINC codes
loincs = Counter((o["loinc_code"], o["loinc_display"]) for o in obs)
print("Top 10 LOINC codes:")
for (code, display), n in loincs.most_common(10):
    print(f"  {code:12s}  {display[:55]:55s}  {n}")
print()

print("Sample BP systolic row:")
if bp_systolic:
    print(bp_systolic[0])
