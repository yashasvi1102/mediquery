import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

tables = ["bronze_patients", "bronze_encounters", "bronze_conditions", "bronze_medication_requests"]
for t in tables:
    result = con.execute(f"SELECT sql FROM duckdb_tables() WHERE schema_name='bronze' AND table_name='{t}'").fetchone()
    print(f"-- {t}")
    print(result[0] if result else "(not found)")
    print()
