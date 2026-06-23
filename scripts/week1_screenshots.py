"""Day 7: Generate the queries we need to screenshot for README and LinkedIn."""
import duckdb

DB = "mediquery.duckdb"
c = duckdb.connect(DB, read_only=True)

print("=" * 60)
print("BRONZE LAYER ROW COUNTS")
print("=" * 60)
rows = c.execute("""
    SELECT 'bronze.bronze_patients'            AS t, COUNT(*) AS n FROM bronze.bronze_patients
    UNION ALL SELECT 'bronze.bronze_encounters',          COUNT(*) FROM bronze.bronze_encounters
    UNION ALL SELECT 'bronze.bronze_conditions',          COUNT(*) FROM bronze.bronze_conditions
    UNION ALL SELECT 'bronze.bronze_medication_requests', COUNT(*) FROM bronze.bronze_medication_requests
    ORDER BY 1
""").fetchall()
for t, n in rows:
    print(f"  {t:38s} {n:>12,} rows")

print()
print("=" * 60)
print("TOP 10 CONDITIONS BY FREQUENCY")
print("=" * 60)
rows = c.execute("""
    SELECT display, COUNT(*) AS n
    FROM bronze.bronze_conditions
    GROUP BY display
    ORDER BY n DESC
    LIMIT 10
""").fetchall()
for display, n in rows:
    print(f"  {display:50s} {n:>8,}")

print()
print("=" * 60)
print("VERIFY: SNOMED 224299000 ('Received higher education')")
print("=" * 60)
rows = c.execute("""
    SELECT display, code, code_system, COUNT(*) AS n
    FROM bronze.bronze_conditions
    WHERE code = '224299000'
    GROUP BY display, code, code_system
""").fetchall()
if rows:
    for display, code, system, n in rows:
        print(f"  {display}  |  {code}  |  {system}  |  {n:,} rows")
else:
    print("  (no rows — find a different oddity for the LinkedIn post)")

c.close()
