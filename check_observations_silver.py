import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- OBS: count + dedup ---")
print(con.execute("select count(*) as n, count(distinct observation_id) as unique_o from silver.silver_observations").fetchdf())

print("--- OBS: observation_kind distribution ---")
print(con.execute("select observation_kind, count(*) as n, count(distinct patient_id) as patients from silver.silver_observations group by 1 order by n desc").fetchdf().to_string())

print("--- OBS: critical values (should be a small fraction) ---")
print(con.execute("select is_critical_value, count(*) as n from silver.silver_observations group by 1").fetchdf())

print("--- OBS: critical values by kind ---")
print(con.execute("select observation_kind, count(*) as n from silver.silver_observations where is_critical_value group by 1 order by n desc").fetchdf().to_string())

print("--- CROSS-CHECK: T2DM patients with HbA1c readings? ---")
print(con.execute("""
    select count(distinct c.patient_id) as t2dm_with_hba1c
    from silver.silver_conditions c
    join silver.silver_observations o on o.patient_id = c.patient_id
    where c.condition_flag = 'diabetes_t2'
      and o.observation_kind = 'hba1c'
""").fetchdf())

print("--- CROSS-CHECK: HbA1c stats for T2DM patients (sanity numbers) ---")
print(con.execute("""
    select
        round(min(o.value_numeric), 2) as min_a1c,
        round(avg(o.value_numeric), 2) as avg_a1c,
        round(max(o.value_numeric), 2) as max_a1c,
        count(*) as readings
    from silver.silver_conditions c
    join silver.silver_observations o on o.patient_id = c.patient_id
    where c.condition_flag = 'diabetes_t2'
      and o.observation_kind = 'hba1c'
""").fetchdf())
