from data_engineering.connection import get_connection

con = get_connection()

print("=== Inpatient encounter distribution (corrected) ===")
print(con.sql("""
    SELECT
      COUNT(*) AS total_inpatient_encounters,
      COUNT(DISTINCT patient_id) AS patients_with_inpatient,
      COUNT(*) * 1.0 / COUNT(DISTINCT patient_id) AS avg_per_patient
    FROM silver.silver_encounters
    WHERE is_inpatient = true
""").fetchdf())


print("\n=== Readmission candidates (2+ inpatient) ===")
print(con.sql("""
    SELECT COUNT(*) AS readmission_candidates
    FROM (
      SELECT patient_id
      FROM silver.silver_encounters
      WHERE is_inpatient = true
      GROUP BY patient_id
      HAVING COUNT(*) >= 2
    )
""").fetchdf())
print("\n=== Encounter class distribution (reconcile Day 10 discrepancy) ===")
print(con.sql("""
    SELECT class_code, is_inpatient, COUNT(*) AS n
    FROM silver.silver_encounters
    GROUP BY class_code, is_inpatient
    ORDER BY n DESC
""").fetchdf())