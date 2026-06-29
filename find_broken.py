import duckdb, json, os
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

# get one broken medication_request_id with patient
row = con.execute("""
    select medication_request_id, patient_id
    from bronze.bronze_medication_requests
    where medication_display is null
    limit 1
""").fetchone()
print(f"Broken med_request_id: {row[0]}")
print(f"Patient: {row[1]}")
print()
print("Now: find the FHIR bundle for this patient in synthea/output/fhir/")
print("Search the JSON for the med_request_id above, look at its structure.")