import duckdb
conn = duckdb.connect("mediquery.duckdb", read_only=True)

print("=== SCHEMAS ===")
print(conn.execute("SELECT schema_name FROM information_schema.schemata").fetchdf().to_string())
print()

print("=== TABLES ===")
print(conn.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY table_schema, table_name").fetchdf().to_string())
print()

for t in ["silver_patients","silver_encounters","silver_conditions","silver_medications"]:
    for schema in ["silver","main","main_silver"]:
        try:
            df = conn.execute(f"DESCRIBE {schema}.{t}").fetchdf()
            print(f"--- {schema}.{t} ---")
            print(df.to_string())
            print()
            break
        except:
            continue