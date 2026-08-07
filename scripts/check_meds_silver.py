import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")

print("--- MEDS: count + dedup ---")
print(con.execute("select count(*) as n, count(distinct medication_request_id) as unique_m from silver.silver_medications").fetchdf())

print("--- MEDS: medication_flag distribution ---")
print(con.execute("select medication_flag, count(*) as n, count(distinct patient_id) as patients from silver.silver_medications where medication_flag is not null group by 1 order by n desc").fetchdf())

print("--- MEDS: drug_class distribution ---")
print(con.execute("select drug_class, count(*) as n from silver.silver_medications group by 1 order by n desc").fetchdf())

print("--- MEDS: active vs completed ---")
print(con.execute("select status, is_active, count(*) as n from silver.silver_medications group by 1,2").fetchdf())

print("--- CROSS-CHECK: diabetes patients on diabetes drugs? ---")
print(con.execute("""
    select count(distinct c.patient_id) as diabetic_patients_treated
    from silver.silver_conditions c
    join silver.silver_medications m on m.patient_id = c.patient_id
    where c.condition_flag = 'diabetes_t2'
      and m.medication_flag = 'diabetes_drug'
""").fetchdf())

print("--- CROSS-CHECK: htn patients on antihypertensive? ---")
print(con.execute("""
    select count(distinct c.patient_id) as htn_patients_treated
    from silver.silver_conditions c
    join silver.silver_medications m on m.patient_id = c.patient_id
    where c.condition_flag = 'hypertension'
      and m.medication_flag = 'antihypertensive'
""").fetchdf())
