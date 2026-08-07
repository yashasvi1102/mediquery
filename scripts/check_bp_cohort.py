import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- BP stats for hypertensive patients (plausible only) ---")
print(con.execute("""
    with htn as (
        select distinct patient_id from silver.silver_conditions where condition_flag = 'hypertension'
    ),
    sys_readings as (
        select o.patient_id, o.value_numeric
        from silver.silver_observations o
        join htn on htn.patient_id = o.patient_id
        where o.loinc_code = '8480-6' and o.is_plausible_value
    )
    select
        count(distinct patient_id) as patients,
        count(*) as readings,
        round(avg(value_numeric), 1) as avg_systolic,
        round(min(value_numeric), 1) as min_systolic,
        round(max(value_numeric), 1) as max_systolic
    from sys_readings
""").fetchdf())

print()
print("--- BP stats for NON-hypertensive patients (control group) ---")
print(con.execute("""
    with htn as (
        select distinct patient_id from silver.silver_conditions where condition_flag = 'hypertension'
    ),
    non_htn_sys as (
        select o.patient_id, o.value_numeric
        from silver.silver_observations o
        left join htn on htn.patient_id = o.patient_id
        where o.loinc_code = '8480-6' and o.is_plausible_value
          and htn.patient_id is null
    )
    select
        count(distinct patient_id) as patients,
        count(*) as readings,
        round(avg(value_numeric), 1) as avg_systolic
    from non_htn_sys
""").fetchdf())
