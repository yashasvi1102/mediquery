"""
fhir_parser.py - First version.

Reads one Synthea FHIR bundle and prints a count of each resource type.
This is a sanity check before we build the real parser.
"""

import json
from collections import Counter
from pathlib import Path

# Path to one Synthea bundle. Change the filename to match yours.
SYNTHEA_OUTPUT = Path(__file__).parent.parent / "synthea" / "output" / "fhir"

def load_bundle(file_path: Path) -> dict:
    """Read a FHIR bundle JSON file and return as a Python dict."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def count_resources(bundle: dict) -> Counter:
    """Count how many of each resourceType appear in the bundle."""
    counts = Counter()
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_type = resource.get("resourceType", "Unknown")
        counts[resource_type] += 1
    return counts

if __name__ == "__main__":
    # Pick the first patient bundle in the output folder.
    # Skip hospitalInformation and practitionerInformation files.
    patient_files = [
        f for f in SYNTHEA_OUTPUT.glob("*.json")
        if not f.name.startswith("hospitalInformation")
        and not f.name.startswith("practitionerInformation")
    ]
    
    if not patient_files:
        print(f"No patient files found in {SYNTHEA_OUTPUT}")
        print("Did Synthea run successfully? Check synthea/output/fhir/")
        exit(1)
    
    first_file = patient_files[0]
    print(f"Reading: {first_file.name}\n")
    
    bundle = load_bundle(first_file)
    counts = count_resources(bundle)
    
    print(f"Total entries: {sum(counts.values())}")
    print(f"Unique resource types: {len(counts)}\n")
    print("Counts by resource type:")
    for resource_type, count in counts.most_common():
        print(f"  {resource_type:30s} {count:5d}")