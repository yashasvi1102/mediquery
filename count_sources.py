import duckdb
conn = duckdb.connect("mediquery.duckdb", read_only=True)

print("=== Source counts for Day 23 ===")
print()

# Row counts
for t in ["silver_patients","silver_encounters","silver_conditions","silver_medications"]:
    n = conn.execute(f"SELECT COUNT(*) FROM silver.{t}").fetchone()[0]
    print(f"{t}: {n:,}")

print()

# Dedup targets (these become node counts)
print("=== Unique node targets ===")
print()

n = conn.execute("SELECT COUNT(DISTINCT snomed_code) FROM silver.silver_conditions").fetchone()[0]
print(f"Unique Condition codes (snomed_code): {n}")

n = conn.execute("SELECT COUNT(DISTINCT rxnorm_code) FROM silver.silver_medications").fetchone()[0]
print(f"Unique Medication codes (rxnorm_code): {n}")

n = conn.execute("SELECT COUNT(DISTINCT provider_id) FROM silver.silver_encounters WHERE provider_id IS NOT NULL").fetchone()[0]
print(f"Unique Providers: {n}")

# Check if provider has any name/detail
sample = conn.execute("SELECT DISTINCT provider_id FROM silver.silver_encounters WHERE provider_id IS NOT NULL LIMIT 3").fetchdf()
print(f"\nSample provider IDs: {sample['provider_id'].tolist()}")

conn.close()
