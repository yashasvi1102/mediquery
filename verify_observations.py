import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- Row counts ---")
for t in ["bronze_patients", "bronze_encounters", "bronze_conditions",
          "bronze_medication_requests", "bronze_observations"]:
    n = con.execute(f"select count(*) from bronze.{t}").fetchone()[0]
    print(f"  {t:30s} {n:>10,}")

print()
print("--- Observations: value type distribution ---")
print(con.execute("""
    select
        count(*) as total,
        sum(case when value_numeric is not null then 1 else 0 end) as numeric_rows,
        sum(case when value_text is not null then 1 else 0 end) as text_rows,
        sum(case when value_code is not null then 1 else 0 end) as coded_rows,
        sum(case when parent_observation_id is not null then 1 else 0 end) as component_rows
    from bronze.bronze_observations
""").fetchdf())

print()
print("--- Top 15 LOINC codes ---")
print(con.execute("""
    select loinc_code, loinc_display, count(*) as n
    from bronze.bronze_observations
    group by 1,2 order by n desc limit 15
""").fetchdf().to_string())

print()
print("--- Blood pressure sanity (systolic vs diastolic counts should match) ---")
print(con.execute("""
    select loinc_code, loinc_display, count(*) as n
    from bronze.bronze_observations
    where loinc_code in ('8480-6','8462-4')
    group by 1,2
""").fetchdf())
