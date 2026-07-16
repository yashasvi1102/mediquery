"""
Baseline detection counts for anomaly benchmark, pre-injection.

Run: python scripts/anomaly_baselines.py

Shipping (Day 20 injection targets):
  - Warfarin + NSAID/aspirin coprescription: baseline 11, inject 30
  - Heart failure 7-day readmission:         baseline 5,  inject 25

Dropped (see DD-005):
  - Chronic-drug persistence gap: refined baseline 2,433 vs 40 target = 1:60
  - Post-discharge no-fill:       refined baseline 470   vs 25 target = 1:19
  Both suffer from DD-004 (Synthea has no MedicationDispense stream). Kept
  in commented block at end of file for reference if MedicationDispense
  parsing is added in a future revision.
"""

import duckdb

con = duckdb.connect('mediquery.duckdb', read_only=True)

# ---------- SHIPPING BASELINES ----------

print("=== Baseline 1: Warfarin + NSAID/aspirin coprescription ===")
print(con.sql("""
    with warfarin_patients as (
        select
            patient_id,
            authored_on as warfarin_start
        from silver.silver_medications
        where medication_display ilike '%warfarin%'
    ),
    concurrent_nsaid_aspirin as (
        select w.patient_id, w.warfarin_start, m.authored_on
        from warfarin_patients w
        inner join silver.silver_medications m
            on w.patient_id = m.patient_id
           and (
                m.medication_display ilike '%aspirin%'
                or m.drug_class = 'nsaid'
           )
           and abs(datediff('day', w.warfarin_start, m.authored_on)) <= 30
    )
    select count(distinct patient_id) as baseline_count
    from concurrent_nsaid_aspirin
""").fetchdf())

print("\n=== Baseline 2: HF 7-day readmission ===")
print(con.sql("""
    select count(*) as baseline_count
    from gold.gold_readmissions
    where is_30_day_readmission = true
      and days_between <= 7
      and is_likely_planned = false
      and (
        index_reason_display ilike '%heart failure%'
        or index_reason_display ilike '%congestive%'
      )
""").fetchdf())


# ---------- DROPPED BASELINES (see DD-005) ----------
# Retained as commented reference. Not called at runtime.
#
# Baseline 3: chronic-drug persistence gap
#   Original baseline:  3,274 (no still-engaged filter)
#   Refined baseline:   2,433 (with still-engaged filter)
#   Target injected:    40
#   Signal:noise ratio: 1:60
#   Dropped Day 19 per DD-005. Root cause DD-004.
#
# Baseline 4: post-discharge no fill
#   Original baseline:  791 (no followup-encounter filter)
#   Refined baseline:   470 (with followup-encounter filter)
#   Target injected:    25
#   Signal:noise ratio: 1:19
#   Dropped Day 19 per DD-005. Root cause DD-004.