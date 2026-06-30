from data_generation.fhir_parser import load_bundle
import json

b = load_bundle(r"synthea\output\fhir\Aaron697_Johns824_bca84d73-f1a6-c867-56b6-c55f493f1ead.json")

# Find the parent observation for one of the empty PRAPARE rows
parent_id = "bca84d73-f1a6-c867-6407-eeab4ddf7caf"
for entry in b.get("entry", []):
    res = entry.get("resource", {})
    if res.get("resourceType") == "Observation" and res.get("id") == parent_id:
        components = res.get("component", [])
        print(f"Parent observation: {res.get('code', {}).get('text')}")
        print(f"Number of components: {len(components)}")
        print()
        print("First component:")
        print(json.dumps(components[0], indent=2))
        break
