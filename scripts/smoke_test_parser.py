from data_generation.fhir_parser import load_bundle, parse_bundle

b = load_bundle(r"synthea\output\fhir\Aaron697_Johns824_bca84d73-f1a6-c867-56b6-c55f493f1ead.json")
parsed = parse_bundle(b)

total = len(parsed["medication_requests"])
nulls = sum(1 for m in parsed["medication_requests"] if m["medication_display"] is None)
print(f"Total medications: {total}")
print(f"NULL medication_display: {nulls}")
print(f"Sample: {parsed['medication_requests'][0]}")