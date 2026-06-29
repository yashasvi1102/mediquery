
import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- ENCOUNTERS: count + dedup check ---")
print(con.execute("select count(*) as n, count(distinct encounter_id) as unique_e from silver.silver_encounters").fetchdf())

print("--- ENCOUNTERS: type distribution ---")
print(con.execute("select encounter_type, count(*) as n from silver.silver_encounters group by 1 order by n desc").fetchdf())

print("--- ENCOUNTERS: inpatient sanity (should be ~12K) ---")
print(con.execute("select count(*) from silver.silver_encounters where is_inpatient").fetchdf())

print("--- CONDITIONS: count + dedup ---")
print(con.execute("select count(*) as n, count(distinct condition_id) as unique_c from silver.silver_conditions").fetchdf())

print("--- CONDITIONS: clinical_category distribution (THE differentiator) ---")
print(con.execute("select clinical_category, count(*) as n from silver.silver_conditions group by 1 order by n desc").fetchdf())

print("--- CONDITIONS: condition_flag distribution ---")
print(con.execute("select condition_flag, count(*) as n, count(distinct patient_id) as patients from silver.silver_conditions where condition_flag is not null group by 1 order by n desc").fetchdf())

print("--- CONDITIONS: billable distribution ---")
print(con.execute("select is_billable_diagnosis, count(*) as n from silver.silver_conditions group by 1").fetchdf())

print("--- CONDITIONS: unknown category top 10 ---")
print(con.execute("select display, count(*) as n from silver.silver_conditions where clinical_category = 'unknown' group by 1 order by n desc limit 10").fetchdf())

