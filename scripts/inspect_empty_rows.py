from data_generation.fhir_parser import load_bundle, parse_bundle
from collections import Counter

b = load_bundle(r"synthea\output\fhir\Aaron697_Johns824_bca84d73-f1a6-c867-56b6-c55f493f1ead.json")
parsed = parse_bundle(b)
obs = parsed["observations"]

empty = [o for o in obs if o["value_numeric"] is None
                       and o["value_text"] is None
                       and o["value_code"] is None]

print(f"Empty rows: {len(empty)}")
print()

# What LOINC codes are they?
loincs = Counter((o["loinc_code"], o["loinc_display"]) for o in empty)
print("Top LOINC codes among empty rows:")
for (code, display), n in loincs.most_common(15):
    print(f"  {code:15s}  {(display or '')[:60]:60s}  {n}")
print()

# Are they all from components?
from_components = sum(1 for o in empty if o["parent_observation_id"] is not None)
print(f"Empty rows that came from a component split: {from_components}")
print(f"Empty rows from non-component observations:  {len(empty) - from_components}")
print()

print("Sample empty row:")
if empty:
    print(empty[0])
