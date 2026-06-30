import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- Plausibility split overall ---")
print(con.execute("select is_plausible_value, count(*) as n from silver.silver_observations group by 1").fetchdf())

print()
print("--- Implausible values by kind ---")
print(con.execute("""
    select observation_kind, count(*) as n
    from silver.silver_observations
    where is_plausible_value = false
    group by 1 order by n desc
""").fetchdf().to_string())

print()
print("--- HbA1c stats: implausible removed ---")
print(con.execute("""
    select
        count(*) as readings,
        round(min(value_numeric), 2) as min_a1c,
        round(avg(value_numeric), 2) as avg_a1c,
        round(max(value_numeric), 2) as max_a1c
    from silver.silver_observations
    where observation_kind = 'hba1c' and is_plausible_value
""").fetchdf())

print()
print("--- T2DM patients HbA1c (plausible only) ---")
print(con.execute("""
    with t2dm as (
        select distinct patient_id from silver.silver_conditions where condition_flag = 'diabetes_t2'
    )
    select
        count(distinct o.patient_id) as patients,
        count(*) as readings,
        round(avg(value_numeric), 2) as avg_a1c
    from silver.silver_observations o
    join t2dm on t2dm.patient_id = o.patient_id
    where o.observation_kind = 'hba1c' and o.is_plausible_value
""").fetchdf())
