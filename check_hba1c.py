import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

# What units does HbA1c report in?
print("--- HbA1c units in the dataset ---")
print(con.execute("""
    select unit, count(*) as n, round(min(value_numeric), 2) as min, round(avg(value_numeric), 2) as avg, round(max(value_numeric), 2) as max
    from silver.silver_observations
    where observation_kind = 'hba1c'
    group by unit
""").fetchdf())

# Distribution: histogram-ish
print()
print("--- HbA1c value bands across ALL patients ---")
print(con.execute("""
    select
        case
            when value_numeric < 4 then '< 4 (impossible)'
            when value_numeric < 5.7 then '4.0-5.7 (normal)'
            when value_numeric < 6.5 then '5.7-6.5 (prediabetic)'
            when value_numeric < 9 then '6.5-9 (diabetic)'
            else '>= 9 (severe diabetic)'
        end as a1c_band,
        count(*) as n
    from silver.silver_observations
    where observation_kind = 'hba1c' and value_numeric is not null
    group by 1 order by 1
""").fetchdf())

# T2DM patients only, deduplicated
print()
print("--- HbA1c for T2DM patients, properly deduplicated ---")
print(con.execute("""
    with t2dm_patients as (
        select distinct patient_id from silver.silver_conditions where condition_flag = 'diabetes_t2'
    ),
    a1c_readings as (
        select o.patient_id, o.value_numeric
        from silver.silver_observations o
        join t2dm_patients p on p.patient_id = o.patient_id
        where o.observation_kind = 'hba1c'
    )
    select
        count(*) as readings,
        count(distinct patient_id) as unique_patients,
        round(avg(value_numeric), 2) as avg_a1c,
        round(min(value_numeric), 2) as min_a1c,
        round(max(value_numeric), 2) as max_a1c
    from a1c_readings
""").fetchdf())
