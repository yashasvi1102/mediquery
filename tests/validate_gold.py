"""
Gold-layer distribution and grain validations.

dbt schema tests catch schema invariants (uniqueness, not-null, enum values,
foreign keys). This file catches what dbt cannot:
  - Distribution claims documented in LEARNINGS.md and design_decisions.md
  - Grain claims that require dbt-utils (not installed as of Day 16)
  - Cross-model reconciliation (patient counts consistent across layers)

Run: python -m tests.validate_gold
"""

import sys
import duckdb

DB_PATH = "mediquery.duckdb"


# ---------- helpers ----------

def scalar(con, sql):
    """Return the first column of the first row."""
    return con.sql(sql).fetchone()[0]


def between(actual, low, high, label):
    """Assert `low <= actual <= high` and print a pass/fail line."""
    ok = low <= actual <= high
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: {actual} (expected {low}-{high})")
    return ok


def equals(actual, expected, label):
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: {actual} (expected {expected})")
    return ok


# ---------- gold_readmissions ----------

def check_readmissions(con):
    print("\n== gold_readmissions ==")
    results = []

    total_pairs = scalar(con, "select count(*) from gold.gold_readmissions")
    results.append(between(total_pairs, 4700, 5000,
                           "total pairs (post-filter, Day 15 = 4,860)"))

    min_days = scalar(con,
        "select min(days_between) from gold.gold_readmissions")
    results.append(equals(min_days, 0, "min days_between (no overlaps)"))

    max_days = scalar(con,
        "select max(days_between) from gold.gold_readmissions")
    results.append(between(max_days, 300, 365,
                           "max days_between (capped at 365)"))

    # Rate progression through the three filters (Day 15 story).
    pct_raw = scalar(con, """
        select round(100.0 * sum(case when is_30_day_readmission then 1 else 0 end) / count(*), 2)
        from gold.gold_readmissions
    """)
    results.append(between(float(pct_raw), 44.0, 46.0,
                           "raw 30-day rate (Day 15 = 45.23%)"))

    pct_clinical_unplanned = scalar(con, """
        select round(100.0 * sum(case when is_30_day_readmission then 1 else 0 end) / count(*), 2)
        from gold.gold_readmissions
        where is_likely_planned = false
          and readmission_reason_is_clinical = true
    """)
    results.append(between(float(pct_clinical_unplanned), 17.0, 22.0,
                           "clinical+unplanned 30-day rate (Day 15 = 19.34%, near CMS 15%)"))

    # Grain: readmission_encounter_id must be unique (dbt already tests this,
    # but keeping here for a single source of truth).
    dup_readmit = scalar(con, """
        select count(*) from (
            select readmission_encounter_id, count(*) as n
            from gold.gold_readmissions
            group by readmission_encounter_id
            having count(*) > 1
        )
    """)
    results.append(equals(dup_readmit, 0, "duplicate readmission_encounter_id"))

    return all(results)


# ---------- gold_chronic_conditions ----------

def check_chronic_conditions(con):
    print("\n== gold_chronic_conditions ==")
    results = []

    # Grain: (patient_id, condition_flag) must be unique.
    # dbt-utils is not installed; enforced here instead.
    dup_pairs = scalar(con, """
        select count(*) from (
            select patient_id, condition_flag, count(*) as n
            from gold.gold_chronic_conditions
            group by patient_id, condition_flag
            having count(*) > 1
        )
    """)
    results.append(equals(dup_pairs, 0,
                          "duplicate (patient_id, condition_flag) pairs"))

    # Patient counts must match Day 10 silver_conditions numbers.
    # Any drift means the condition_flag or NULL filter changed upstream.
    diabetes_patients = scalar(con, """
        select count(distinct patient_id)
        from gold.gold_chronic_conditions
        where condition_flag = 'diabetes_t2'
    """)
    results.append(between(diabetes_patients, 1700, 1760,
                           "diabetes_t2 patients (Day 10 = 1,731)"))

    htn_patients = scalar(con, """
        select count(distinct patient_id)
        from gold.gold_chronic_conditions
        where condition_flag = 'hypertension'
    """)
    results.append(between(htn_patients, 2600, 2730,
                           "hypertension patients (Day 10 = 2,665)"))

    hf_patients = scalar(con, """
        select count(distinct patient_id)
        from gold.gold_chronic_conditions
        where condition_flag = 'heart_failure'
    """)
    results.append(between(hf_patients, 300, 340,
                           "heart_failure patients (Day 10 = 321)"))

    copd_patients = scalar(con, """
        select count(distinct patient_id)
        from gold.gold_chronic_conditions
        where condition_flag = 'copd'
    """)
    results.append(between(copd_patients, 150, 180,
                           "copd patients (Day 10 = 164)"))

    # Comorbidity distribution — sanity, not a hard claim.
    max_comorbidity = scalar(con,
        "select max(comorbidity_count) from gold.gold_chronic_conditions")
    results.append(between(max_comorbidity, 2, 4,
                           "max comorbidity_count (4 chronic flags tracked)"))

    # Every row must join back to silver_patients cleanly.
    orphan_patients = scalar(con, """
        select count(*)
        from gold.gold_chronic_conditions gc
        left join silver.silver_patients sp using (patient_id)
        where sp.patient_id is null
    """)
    results.append(equals(orphan_patients, 0,
                          "orphan patient_ids (should be 0)"))

    # Diabetes T2 patients typically carry 2-3 Silver rows (Day 10 note:
    # 4,189 rows / 1,731 patients = 2.4). This should hold in Gold too.
    avg_diabetes_rows = scalar(con, """
        select round(avg(diagnosis_row_count), 2)
        from gold.gold_chronic_conditions
        where condition_flag = 'diabetes_t2'
    """)
    results.append(between(float(avg_diabetes_rows), 2.0, 3.0,
                           "avg diagnosis_row_count for diabetes_t2 (Day 10 = 2.4)"))

    return all(results)

def check_medication_adherence(con):
    print("\n== gold_medication_adherence ==")
    results = []

    # Row counts by class must match sanity-check output.
    total_rows = scalar(con,
        "select count(*) from gold.gold_medication_adherence")
    results.append(between(total_rows, 8000, 9500,
                           "total patient-class pairs (Day 17 = 8,546)"))

    # DD-004: bimodal PDC. At least 60% of pairs must fall below 0.25 and
    # at least 15% must be >= 0.80 (the "sparse vs dense" split).
    pct_below_25 = scalar(con, """
        select round(100.0 * count(*) filter (where pdc < 0.25) / count(*), 1)
        from gold.gold_medication_adherence
        where pdc is not null
    """)
    results.append(between(float(pct_below_25), 60.0, 70.0,
                           "%% PDC < 0.25 (DD-004 sparse tail, Day 17 = 63.5%)"))

    pct_gte_80 = scalar(con, """
        select round(100.0 * count(*) filter (where pdc >= 0.80) / count(*), 1)
        from gold.gold_medication_adherence
        where pdc is not null
    """)
    results.append(between(float(pct_gte_80), 15.0, 25.0,
                           "%% PDC >= 0.80 (DD-004 dense tail, Day 17 = 19.9%)"))

    # Persistence is strong. Median HTN persistence >= 5 years.
    htn_median_persistence = scalar(con, """
        select percentile_cont(0.5) within group (order by persistence_days)
        from gold.gold_medication_adherence
        where medication_flag = 'antihypertensive'
    """)
    results.append(between(htn_median_persistence, 1825, 4000,
                           "HTN median persistence days (Day 17 = 3,612)"))

    # No orphan patient_ids.
    orphans = scalar(con, """
        select count(*)
        from gold.gold_medication_adherence gma
        left join silver.silver_patients sp using (patient_id)
        where sp.patient_id is null
    """)
    results.append(equals(orphans, 0, "orphan patient_ids"))

    # PDC bounds. If min < 0 or max > 1, math is wrong.
    min_pdc = scalar(con,
        "select min(pdc) from gold.gold_medication_adherence where pdc is not null")
    max_pdc = scalar(con,
        "select max(pdc) from gold.gold_medication_adherence where pdc is not null")
    results.append(between(min_pdc, 0.0, 0.1, "min PDC (should be near 0)"))
    results.append(equals(max_pdc, 1.0, "max PDC (should cap at 1.0)"))

    return all(results)
def check_utilization(con):
    print("\n== gold_utilization ==")
    results = []

    # Row count must match silver_patients exactly.
    silver_count = scalar(con, "select count(*) from silver.silver_patients")
    gold_count   = scalar(con, "select count(*) from gold.gold_utilization")
    results.append(equals(gold_count, silver_count,
                          f"row count == silver_patients ({silver_count})"))

    # Encounter type sum must match silver_encounters total.
    silver_encounters = scalar(con,
        "select count(*) from silver.silver_encounters")
    gold_encounter_sum = scalar(con, """
        select sum(total_encounters) from gold.gold_utilization
    """)
    results.append(equals(gold_encounter_sum, silver_encounters,
                          f"sum(total_encounters) == silver_encounters ({silver_encounters})"))

    # Inpatient encounters should sum to Day 10's 12,223.
    gold_inpatient_sum = scalar(con,
        "select sum(inpatient_encounters) from gold.gold_utilization")
    results.append(equals(gold_inpatient_sum, 12223,
                          "sum(inpatient_encounters) (Day 10 = 12,223)"))

    return all(results)


def check_provider_volume(con):
    print("\n== gold_provider_volume ==")
    results = []

    # Row count == distinct providers in silver.
    silver_providers = scalar(con,
        "select count(distinct provider_id) from silver.silver_encounters where provider_id is not null")
    gold_providers = scalar(con, "select count(*) from gold.gold_provider_volume")
    results.append(equals(gold_providers, silver_providers,
                          f"provider count == silver distinct providers ({silver_providers})"))

    # Encounter sum must reconcile to silver.
    silver_encounters = scalar(con,
        "select count(*) from silver.silver_encounters where provider_id is not null")
    gold_encounter_sum = scalar(con,
        "select sum(total_encounters) from gold.gold_provider_volume")
    results.append(equals(gold_encounter_sum, silver_encounters,
                          f"sum(total_encounters) == silver provider-attributed encounters"))

    # Top provider sanity: at least one provider with 10K+ encounters.
    max_encounters = scalar(con,
        "select max(total_encounters) from gold.gold_provider_volume")
    results.append(between(max_encounters, 10000, 25000,
                           "max provider encounters (top provider volume)"))

    return all(results)
def check_anomaly_injection(con):
    print("\n== gold.ground_truth_anomalies (Day 20 injection) ==")
    results = []

    # Total injected count matches Day 20 targets.
    total = scalar(con, "select count(*) from gold.ground_truth_anomalies")
    results.append(equals(total, 55, "total ground_truth rows (30 warfarin + 25 HF)"))

    warfarin_gt = scalar(con, """
        select count(*) from gold.ground_truth_anomalies
        where anomaly_type = 'warfarin_antiplatelet_no_monitoring'
    """)
    results.append(equals(warfarin_gt, 30, "warfarin ground_truth count"))

    hf_gt = scalar(con, """
        select count(*) from gold.ground_truth_anomalies
        where anomaly_type = 'heart_failure_early_readmission'
    """)
    results.append(equals(hf_gt, 25, "HF ground_truth count"))

    # Post-injection detection queries must return baseline + injected.
    warfarin_detected = scalar(con, """
        with warfarin_patients as (
            select patient_id, authored_on as warfarin_start
            from silver.silver_medications
            where medication_display ilike '%warfarin%'
        ),
        concurrent as (
            select w.patient_id
            from warfarin_patients w
            inner join silver.silver_medications m
                on w.patient_id = m.patient_id
               and (
                    m.medication_display ilike '%aspirin%'
                    or m.drug_class = 'nsaid'
               )
               and abs(datediff('day', w.warfarin_start, m.authored_on)) <= 30
        )
        select count(distinct patient_id) from concurrent
    """)
    results.append(equals(warfarin_detected, 41,
                          "warfarin detection (baseline 11 + injected 30)"))

    hf_detected = scalar(con, """
        select count(*)
        from gold.gold_readmissions
        where is_30_day_readmission = true
          and days_between <= 7
          and is_likely_planned = false
          and (
            index_reason_display ilike '%heart failure%'
            or index_reason_display ilike '%congestive%'
          )
    """)
    results.append(equals(hf_detected, 30,
                          "HF 7-day detection (baseline 5 + injected 25)"))

    # Every injected patient must exist in silver_patients (integrity check).
    orphans = scalar(con, """
        select count(*)
        from gold.ground_truth_anomalies gta
        left join silver.silver_patients sp using (patient_id)
        where sp.patient_id is null
    """)
    results.append(equals(orphans, 0, "orphan ground_truth patient_ids"))

    return all(results)
# ---------- entry point ----------

def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    print(f"Validating gold layer against {DB_PATH}")

    checks = [
        check_readmissions(con),
        check_chronic_conditions(con),
        check_medication_adherence(con),
        check_utilization(con),
        check_provider_volume(con),
        check_anomaly_injection(con),
        
    ]

    print()
    if all(checks):
        print("All gold validations passed.")
        sys.exit(0)
    else:
        print("One or more gold validations FAILED. See output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()