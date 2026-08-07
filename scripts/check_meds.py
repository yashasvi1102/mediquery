import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- TOP 20 MEDICATIONS ---")
print(con.execute("select medication_display, count(*) as n from bronze.bronze_medication_requests group by 1 order by n desc limit 20").fetchdf().to_string())

print("--- NULL code_system: what do those look like? ---")
print(con.execute("select medication_display, count(*) as n from bronze.bronze_medication_requests where code_system is null group by 1 order by n desc limit 10").fetchdf().to_string())

print("--- TARGETED DRUGS (do they exist in dataset?) ---")
for drug in ["metformin", "lisinopril", "furosemide", "albuterol", "losartan", "amlodipine", "carvedilol", "fluticasone", "hydrochlorothiazide"]:
    r = con.execute(f"select count(*) from bronze.bronze_medication_requests where lower(medication_display) like '%{drug}%'").fetchone()
    print(f"  {drug}: {r[0]}")

print("--- SAMPLE ROW ---")
print(con.execute("select * from bronze.bronze_medication_requests limit 2").fetchdf().to_string())