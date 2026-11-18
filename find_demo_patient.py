import duckdb
conn = duckdb.connect("mediquery.duckdb", read_only=True)
print(conn.execute("""
    SELECT p.patient_id, p.given_name, p.family_name, p.age_years_current,
           COUNT(DISTINCT c.snomed_code) as conditions,
           COUNT(DISTINCT m.rxnorm_code) as medications
    FROM silver.silver_patients p
    JOIN silver.silver_conditions c ON p.patient_id = c.patient_id
    JOIN silver.silver_medications m ON p.patient_id = m.patient_id
    WHERE c.condition_flag IN ('diabetes_t2','hypertension')
    AND p.age_years_current BETWEEN 50 AND 70
    AND p.is_deceased = false
    GROUP BY 1,2,3,4
    ORDER BY conditions DESC
    LIMIT 5
""").fetchdf().to_string())
