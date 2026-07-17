"""
Anomaly Injection Framework — Day 20 implementation.

Injects known clinical anomalies into Silver tables for the Week 6 AI agent
benchmark. Records ground truth in gold.ground_truth_anomalies.

Two anomaly types (see docs/anomaly_framework.md and DD-005):
  1. Warfarin + NSAID/aspirin coprescription (30 injections)
  2. HF 7-day readmission (25 injections)

Idempotency: injection is keyed by injection_batch_id. Passing the same
batch_id deletes previously injected rows before reinserting.

Usage:
    from data_generation.anomaly_injector import inject_all
    inject_all(batch_id='day20_initial')
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

# --- Path resolution -----------------------------------------------------------
# Script may be invoked from repo root or from data_generation/. Anchor to the
# DB path relative to this file, not CWD.
DB_PATH = Path(__file__).resolve().parent.parent / 'mediquery.duckdb'

# --- Reproducibility -----------------------------------------------------------
# Seeded so re-runs produce the same patients/dates. Deterministic testing.
random.seed(20260720)  # arbitrary — locked so injections are reproducible


# ============================================================================
# INJECTOR 1: Warfarin + NSAID/aspirin coprescription
# ============================================================================

def inject_warfarin_coprescription(con, batch_id, n=30):
    """
    Inject n patients with concurrent warfarin + aspirin (or ibuprofen)
    prescriptions. Both rows attached to the patient's most recent
    ambulatory encounter for realism.

    Target selection: patients aged >=55 with no existing warfarin,
    aspirin, or NSAID prescription.
    """
    print(f"\n[Injector 1] Warfarin + NSAID/aspirin coprescription (n={n})")

    # Step 1: find candidate patients
    candidates = con.sql("""
        with eligible as (
            select
                sp.patient_id,
                sp.age_years_current
            from silver.silver_patients sp
            where sp.age_years_current >= 55
              and sp.patient_id not in (
                  select patient_id
                  from silver.silver_medications
                  where medication_display ilike '%warfarin%'
                     or medication_display ilike '%aspirin%'
                     or drug_class = 'nsaid'
              )
        )
        select
            e.patient_id,
            max(se.encounter_id)              as anchor_encounter_id,
            max(se.provider_id)               as anchor_provider_id,
            max(se.start_time)                as anchor_encounter_start
        from eligible e
        inner join silver.silver_encounters se
            on e.patient_id = se.patient_id
           and se.encounter_type = 'ambulatory'
        group by e.patient_id
        having max(se.start_time) is not null
    """).fetchdf()

    if len(candidates) < n:
        raise RuntimeError(
            f"Only {len(candidates)} candidate patients meet criteria. "
            f"Cannot inject {n}."
        )

    # Step 2: sample n patients
    selected = candidates.sample(n=n, random_state=42).to_dict('records')

    # Step 3: prepare row payloads
    warfarin_rows = []
    aspirin_rows  = []
    ground_truth  = []
    now_ts = datetime.utcnow()

    for pt in selected:
        warfarin_id = f'anomaly_med_{uuid.uuid4()}'
        aspirin_id  = f'anomaly_med_{uuid.uuid4()}'
        anomaly_id  = f'anomaly_{uuid.uuid4()}'

        # Realistic dates: warfarin authored on encounter date, aspirin 3-14 days later
        warfarin_date = pt['anchor_encounter_start']
        aspirin_date  = warfarin_date + timedelta(days=random.randint(3, 14))

        warfarin_rows.append((
            warfarin_id,                                  # medication_request_id
            pt['patient_id'],                             # patient_id
            pt['anchor_encounter_id'],                    # encounter_id
            'active',                                     # status
            'order',                                      # intent
            'RxNorm',                                     # code_system
            '855332',                                     # rxnorm_code (warfarin 5mg)
            'Warfarin Sodium 5 MG Oral Tablet',           # medication_display
            None,                                         # medication_flag (not chronic-tracked)
            True,                                         # is_active
            'other',                                      # drug_class (matches Silver taxonomy)
            warfarin_date,                                # authored_on
            'Take 1 tablet daily',                        # dosage_text
            now_ts,                                       # load_timestamp
            batch_id,                                     # load_batch_id
        ))

        # Alternate between aspirin (drug_class=other) and ibuprofen (drug_class=nsaid)
        # so the detector must handle both display-based and class-based detection.
        if random.random() < 0.6:
            drug = ('243670', 'aspirin 81 MG Oral Capsule [Vazalore]', 'other', 'Take 1 daily')
        else:
            drug = ('310965', 'Ibuprofen 400 MG Oral Tablet [Ibu]',    'nsaid', 'Take as needed')

        rxnorm, display, dclass, dosage = drug

        aspirin_rows.append((
            aspirin_id,
            pt['patient_id'],
            pt['anchor_encounter_id'],
            'active',
            'order',
            'RxNorm',
            rxnorm,
            display,
            None,
            True,
            dclass,
            aspirin_date,
            dosage,
            now_ts,
            batch_id,
        ))

        ground_truth.append((
            anomaly_id,
            'warfarin_antiplatelet_no_monitoring',
            pt['patient_id'],
            now_ts,
            batch_id,
            json.dumps({
                'warfarin_medication_request_id': warfarin_id,
                'partner_medication_request_id':  aspirin_id,
                'warfarin_date':                  str(warfarin_date),
                'partner_date':                   str(aspirin_date),
                'partner_drug':                   display,
                'overlap_start':                  str(aspirin_date),
                'anchor_encounter_id':            pt['anchor_encounter_id'],
            }),
            False,   # detected_by_agent
            None,    # detected_at
        ))

    # Step 4: idempotent delete-then-insert
    con.execute(
        "delete from silver.silver_medications where load_batch_id = ?",
        [batch_id]
    )
    con.execute(
        "delete from gold.ground_truth_anomalies where injection_batch_id = ? "
        "and anomaly_type = 'warfarin_antiplatelet_no_monitoring'",
        [batch_id]
    )

    con.executemany(
        """insert into silver.silver_medications values
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        warfarin_rows + aspirin_rows
    )
    con.executemany(
        """insert into gold.ground_truth_anomalies values
           (?, ?, ?, ?, ?, ?, ?, ?)""",
        ground_truth
    )

    print(f"  Injected {len(warfarin_rows)} warfarin rows, "
          f"{len(aspirin_rows)} partner rows, "
          f"{len(ground_truth)} ground_truth records.")


# ============================================================================
# INJECTOR 2: Heart failure 7-day readmission
# ============================================================================

def inject_hf_early_readmission(con, batch_id, n=25):
    """
    Inject n patients with an HF-related 7-day readmission. New inpatient
    encounter authored 2-6 days after an existing HF inpatient encounter's
    end_time, plus a matching Condition row so downstream joins remain
    consistent.

    Target selection: patients with condition_flag='heart_failure' who have
    exactly ONE prior inpatient HF encounter.
    """
    print(f"\n[Injector 2] HF 7-day readmission (n={n})")

    candidates = con.sql("""
        with hf_patients as (
            select distinct patient_id
            from gold.gold_chronic_conditions
            where condition_flag = 'heart_failure'
        ),
        hf_inpatient_encounters as (
            select
                se.patient_id,
                se.encounter_id,
                se.provider_id,
                se.end_time,
                se.reason_code,
                row_number() over (partition by se.patient_id order by se.end_time desc) as rn,
                count(*)      over (partition by se.patient_id)                          as inpatient_count
            from silver.silver_encounters se
            inner join hf_patients hp using (patient_id)
            where se.is_inpatient = true
              and (
                se.reason_display ilike '%heart failure%'
                or se.reason_display ilike '%congestive%'
              )
        )
        select
            patient_id,
            encounter_id      as index_encounter_id,
            provider_id       as index_provider_id,
            end_time          as index_end_time
        from hf_inpatient_encounters
        where inpatient_count = 1
          and rn = 1
    """).fetchdf()

    if len(candidates) < n:
        raise RuntimeError(
            f"Only {len(candidates)} candidate HF patients. Cannot inject {n}."
        )

    selected = candidates.sample(n=n, random_state=43).to_dict('records')

    encounter_rows = []
    condition_rows = []
    ground_truth   = []
    now_ts = datetime.utcnow()

    for pt in selected:
        readmit_encounter_id = f'anomaly_enc_{uuid.uuid4()}'
        readmit_condition_id = f'anomaly_cond_{uuid.uuid4()}'
        anomaly_id           = f'anomaly_{uuid.uuid4()}'

        days_gap = random.randint(2, 6)
        stay_len = random.randint(2, 5)
        start_ts = pt['index_end_time'] + timedelta(days=days_gap)
        end_ts   = start_ts + timedelta(days=stay_len)

        # silver_encounters columns:
        # encounter_id, patient_id, provider_id, status, class_code, encounter_type,
        # is_inpatient, type_code, type_display, reason_code, reason_display,
        # start_time, end_time, duration_minutes, length_of_stay_days,
        # load_timestamp, load_batch_id
        encounter_rows.append((
            readmit_encounter_id,
            pt['patient_id'],
            pt['index_provider_id'],
            'finished',
            'IMP',
            'inpatient',
            True,
            None,                     # type_code
            None,                     # type_display
            '88805009',               # SNOMED for CHF
            'Chronic congestive heart failure (disorder)',
            start_ts,
            end_ts,
            stay_len * 24 * 60,       # duration_minutes
            stay_len,                 # length_of_stay_days
            now_ts,
            batch_id,
        ))

        # silver_conditions columns:
        # condition_id, patient_id, encounter_id, snomed_code, display,
        # clinical_category, condition_flag, is_billable_diagnosis,
        # clinical_status, verification_status,
        # onset_date, abatement_date, recorded_date, is_active,
        # load_timestamp, load_batch_id
        condition_rows.append((
            readmit_condition_id,
            pt['patient_id'],
            readmit_encounter_id,
            '88805009',
            'Chronic congestive heart failure (disorder)',
            'disorder',
            'heart_failure',
            True,
            'active',
            'confirmed',
            start_ts,
            None,
            start_ts,
            True,
            now_ts,
            batch_id,
        ))

        ground_truth.append((
            anomaly_id,
            'heart_failure_early_readmission',
            pt['patient_id'],
            now_ts,
            batch_id,
            json.dumps({
                'index_encounter_id':       pt['index_encounter_id'],
                'index_end_time':           str(pt['index_end_time']),
                'readmission_encounter_id': readmit_encounter_id,
                'readmission_condition_id': readmit_condition_id,
                'days_between':             days_gap,
                'length_of_stay_days':      stay_len,
            }),
            False,
            None,
        ))

    # Idempotent delete-then-insert
    con.execute(
        "delete from silver.silver_encounters where load_batch_id = ?",
        [batch_id]
    )
    con.execute(
        "delete from silver.silver_conditions where load_batch_id = ?",
        [batch_id]
    )
    con.execute(
        "delete from gold.ground_truth_anomalies where injection_batch_id = ? "
        "and anomaly_type = 'heart_failure_early_readmission'",
        [batch_id]
    )

    con.executemany(
        """insert into silver.silver_encounters values
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        encounter_rows
    )
    con.executemany(
        """insert into silver.silver_conditions values
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        condition_rows
    )
    con.executemany(
        """insert into gold.ground_truth_anomalies values
           (?, ?, ?, ?, ?, ?, ?, ?)""",
        ground_truth
    )

    print(f"  Injected {len(encounter_rows)} readmission encounters, "
          f"{len(condition_rows)} matching conditions, "
          f"{len(ground_truth)} ground_truth records.")


# ============================================================================
# Orchestration
# ============================================================================

def inject_all(batch_id):
    """Run both injectors under a shared batch_id."""
    print(f"Anomaly injection — batch_id={batch_id}")
    print(f"DB: {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    try:
        inject_warfarin_coprescription(con, batch_id, n=30)
        inject_hf_early_readmission(con,      batch_id, n=25)
        con.commit()
        print("\nAll injections committed.")
    except Exception:
        con.rollback()
        print("\nInjection FAILED. All changes rolled back.")
        raise
    finally:
        con.close()


if __name__ == '__main__':
    inject_all(batch_id='day20_initial')