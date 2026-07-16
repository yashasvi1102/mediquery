# Anomaly Injection Framework

## Purpose

The MediQuery AI agent (Week 5) is evaluated on its ability to detect clinical
anomalies in patient data. Real anomaly detection benchmarks require ground
truth — a known set of anomalies with known counts. This framework:

1. Defines four clinically-motivated anomaly types.
2. Injects known instances into Silver-layer tables with recorded ground truth.
3. Measures baseline false-positive counts pre-injection so precision/recall
   post-injection is meaningful, not measured against noise.

## Scope decisions

Two anomaly types from the original plan were dropped and replaced:

| Anomaly | Status | Reason |
|---|---|---|
| Missed annual screening for diabetics | Dropped | DD-002: Synthea observation values not clinically valid |
| Critical lab values without follow-up | Dropped | Same |
| Chronic-drug persistence gap | Added | Prescription-pattern; Synthea models this well (DD-004 persistence work) |
| Post-discharge medication non-fill | Added | Prescription-pattern; measurable in Silver |

The prescription-pattern anomalies work on Synthea for the same reason DD-004
persistence works: Synthea generates MedicationRequest reliably, even if it
doesn't generate MedicationDispense.

## Injection principles

**Realism.** Injected rows must be statistically indistinguishable from natural
Synthea rows except in the specific dimension being tested. Same date ranges,
realistic dosages, real provider_ids from silver_encounters, same code systems.
No `patient_id = "ANOMALY_001"` shortcuts.

**Traceability.** Every injected row references an anomaly_id in
gold.ground_truth_anomalies. Injection is idempotent — rerunning with the same
batch_id updates in place, doesn't duplicate.

**Ground truth isolation.** Baseline false-positive counts are captured
BEFORE injection. Post-injection precision/recall = (injected_detected) /
(injected_total); recall must account for the baseline pre-existing cases.

**Volume.** 25-50 injections per type. Enough for statistically meaningful
precision/recall on an 11,446-patient cohort, small enough to stay clinically
plausible (real hospital adverse event rates are 3-10% depending on category).

---

## Anomaly Type 1: Warfarin-Antiplatelet Coprescription Without Monitoring

### Clinical significance

Concurrent warfarin + aspirin or NSAID doubles-to-triples major bleeding risk.
Standard of care: INR monitoring every 2-4 weeks during co-prescription.
Real EHRs flag this via drug-drug interaction alerts. Studies estimate
15-30% of warfarin patients have inappropriate antiplatelet coprescription
in outpatient settings.

### Detection criterion

Patient has:
- Active warfarin prescription (RxNorm 855332 or drug_class = 'anticoagulant')
- Overlapping active aspirin/NSAID prescription
- Overlap window ≥ 7 days
- No INR observation (LOINC 6301-6 or 34714-6) recorded during the overlap window

### Injection strategy

- **Target patients:** 30 patients aged ≥55, currently NOT on warfarin, NOT on
  antiplatelet drugs, with at least one encounter in the last 5 years.
- **Rows inserted (per patient):**
  - `silver_medications` row: warfarin, RxNorm 855332, drug_class =
    'anticoagulant', authored_on = random date in 2015-2020, status = 'active'
  - `silver_medications` row: aspirin 81mg, RxNorm 243670, drug_class =
    'antiplatelet' (or nsaid), authored_on = warfarin_date + 3-14 days
- **Rows NOT inserted:** no matching silver_observations for INR/LOINC.

### Ground truth

30 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'warfarin_antiplatelet_no_monitoring'`.

### Baseline detection (pre-injection)

Detection query below. Baseline count captures existing Synthea patients who
already match the criterion without injection.

```sql
with warfarin_patients as (
    select distinct patient_id, min(authored_on) as first_warfarin
    from silver.silver_medications
    where drug_class = 'anticoagulant'
      and medication_display ilike '%warfarin%'
    group by patient_id
),
antiplatelet_overlap as (
    select w.patient_id
    from warfarin_patients w
    inner join silver.silver_medications m
        on w.patient_id = m.patient_id
       and m.drug_class in ('antiplatelet')  -- adjust if drug_class taxonomy differs
       and abs(datediff('day', w.first_warfarin, m.authored_on)) <= 30
)
select count(distinct patient_id) as baseline_count from antiplatelet_overlap;
```

**Baseline count:** _TBD (run query, fill in)_
**Target injected:** 30
**Expected post-injection:** baseline + 30

**Caveat:** if your silver_medications.drug_class doesn't have an 'antiplatelet'
value, this query returns 0 baseline — meaning any post-injection hits are all
injected (perfect recall, but precision measurement depends on no natural
noise). Verify drug_class values before injecting.

---

## Anomaly Type 2: Heart Failure 7-Day Readmission

### Clinical significance

CMS penalizes hospitals for HF readmissions within 30 days. 7-day readmissions
signal severe issues — inadequate discharge planning, unresolved decompensation,
or medication non-reconciliation. Real HF 30-day readmission rate is ~20%;
7-day rate is ~5%.

### Detection criterion

Patient with heart_failure condition_flag has two inpatient encounters where:
- Index encounter reason mentions "heart failure" or "congestive"
- Readmission encounter starts ≤ 7 days after index discharge
- Readmission is not a planned procedure (is_likely_planned = false)

### Injection strategy

- **Target patients:** 25 patients with condition_flag = 'heart_failure' who
  have exactly ONE inpatient encounter for HF in their history.
- **Rows inserted (per patient):**
  - `silver_encounters` row: inpatient, class_code = 'IMP', is_inpatient = true,
    reason_display = 'Chronic congestive heart failure (disorder)',
    reason_code = matching SNOMED,
    start_time = (existing HF encounter end_time) + 2-6 days,
    end_time = start_time + 2-5 days
  - New encounter uses real provider_id sampled from providers currently
    treating HF patients.

### Ground truth

25 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'heart_failure_early_readmission'`. Details JSON includes
both encounter_ids.

### Baseline detection (pre-injection)

Reuses gold_readmissions.

```sql
select count(*) as baseline_count
from gold.gold_readmissions
where is_30_day_readmission = true
  and days_between <= 7
  and is_likely_planned = false
  and (
    index_reason_display ilike '%heart failure%'
    or index_reason_display ilike '%congestive%'
  );
```

**Baseline count:** 5 (measured 2026-XX-XX)
**Target injected:** 25
**Expected post-injection:** 30

Note: gold_readmissions is a downstream model. After injection, rerun
`dbt run --select gold_readmissions` to propagate the injected encounters.

---

## Anomaly Type 3: Chronic-Drug Persistence Gap

### Clinical significance

A patient on a chronic maintenance drug (metformin, ACE inhibitor, statin)
who stops filling prescriptions for >180 days is at high risk of disease
progression. Real EHRs flag "abandoned" chronic prescriptions as a quality
metric. Adherence programs target these patients for outreach.

### Detection criterion

Patient with chronic condition_flag (diabetes_t2, hypertension, heart_failure,
copd) had ≥3 prescriptions of the corresponding drug_class in a rolling
12-month window, then a gap ≥180 days before the next prescription (or before
observation_end if no further prescriptions).

### Injection strategy

- **Target patients:** 40 patients drawn from those currently classified as
  'adherent' or 'partial' in gold_medication_adherence for one of the four
  drug classes.
- **Rows modified:**
  - Delete the most recent 3-5 prescriptions of the target drug_class for
    the patient.
  - Do NOT insert replacement rows — the gap is the anomaly.
- **Downstream impact:** rerun gold_medication_adherence after injection.

### Ground truth

40 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'chronic_drug_persistence_gap'`. Details JSON includes
patient's chronic condition, drug_class, and last remaining prescription date.

### Baseline detection (pre-injection)

```sql
with last_fills as (
    select
        patient_id,
        drug_class,
        max(authored_on) as last_prescription,
        count(*)         as n_prescriptions
    from silver.silver_medications
    where medication_flag is not null
    group by patient_id, drug_class
    having count(*) >= 3
)
select count(*) as baseline_count
from last_fills
where datediff('day', last_prescription, current_timestamp) >= 180
  and datediff('day', last_prescription, current_timestamp) <= 3650;  -- exclude ancient
```

**Baseline count:** _TBD_
**Target injected:** 40
**Expected post-injection:** baseline + 40

**Caveat:** DD-004 says Synthea prescriptions are encounter-tied, not
dispense-based. Many "gaps" in Synthea are natural (patient just didn't have
a visit for 2 years). Baseline count could be VERY high. If baseline > 500,
tighten the criterion to require patient was recently seen (encounter in last
365 days) — otherwise we can't distinguish injected gaps from natural sparsity.

---

## Anomaly Type 4: Post-Discharge Medication Non-Fill

### Clinical significance

A patient discharged from an inpatient stay with a NEW medication started
during admission (e.g., new beta-blocker after MI, new anticoagulant after
DVT) should fill it within 14 days. Non-fills are a documented cause of
readmission — 20-30% of post-MI patients don't fill discharge cardiac meds.

### Detection criterion

Patient had inpatient encounter with a MedicationRequest authored during the
encounter for a drug in a chronic class (diabetes_drug, antihypertensive,
heart_failure_drug, copd_drug) AND no subsequent MedicationRequest for the
same drug_class within 90 days post-discharge.

### Injection strategy

- **Target patients:** 25 patients selected from those with inpatient
  encounters and no existing chronic-drug prescriptions.
- **Rows inserted:**
  - `silver_medications` row authored during an existing inpatient encounter:
    drug_class = 'beta_blocker' or 'ace_inhibitor', authored_on = encounter
    start_time + 1-3 days, medication_flag = 'antihypertensive'
- **Rows NOT inserted:** no follow-up prescription within 90 days.

### Ground truth

25 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'post_discharge_no_fill'`. Details JSON includes discharge
encounter_id and initial drug_class.

### Baseline detection (pre-injection)

```sql
with discharge_prescriptions as (
    select
        e.patient_id,
        e.encounter_id,
        e.end_time as discharge_date,
        m.drug_class,
        m.authored_on
    from silver.silver_encounters e
    inner join silver.silver_medications m
        on e.encounter_id = m.encounter_id
    where e.is_inpatient = true
      and m.medication_flag is not null
),
followup_fills as (
    select
        dp.patient_id,
        dp.discharge_date,
        dp.drug_class,
        count(m2.medication_request_id) as followup_count
    from discharge_prescriptions dp
    left join silver.silver_medications m2
        on dp.patient_id = m2.patient_id
       and m2.drug_class = dp.drug_class
       and m2.authored_on > dp.discharge_date
       and m2.authored_on <= dp.discharge_date + interval '90 days'
    group by dp.patient_id, dp.discharge_date, dp.drug_class
)
select count(*) as baseline_count
from followup_fills
where followup_count = 0;
```

**Baseline count:** _TBD_
**Target injected:** 25
**Expected post-injection:** baseline + 25

**Caveat:** Synthea's encounter-tied prescription model (DD-004) means many
inpatient prescriptions naturally lack follow-up if the patient's next
encounter is >90 days out. Baseline could be very high. If baseline > 300,
restrict to patients with encounters within 90 days of discharge who still
didn't get a follow-up prescription — closer to real clinical non-fill.

---

## Baseline Summary Table

Fill in as each baseline query runs.

| Anomaly type | Baseline | Target injected | Expected post-injection |
|---|---|---|---|
| Warfarin-antiplatelet | TBD | 30 | TBD + 30 |
| HF 7-day readmission | 5 | 25 | 30 |
| Chronic-drug persistence gap | TBD | 40 | TBD + 40 |
| Post-discharge no fill | TBD | 25 | TBD + 25 |

**Precision/recall formulas (Day 39 benchmark):**

- Recall = (agent-detected-injected) / (total-injected)
- Precision = (agent-detected-injected) / (agent-detected-total)
- Where agent-detected-total = agent-detected-injected + agent-detected-baseline

Baseline detections are NOT false positives — they're real clinical patterns.
But they can't count toward recall since they weren't in the ground truth set.

---

## `gold.ground_truth_anomalies` schema

Add to `data_engineering/schema/bronze_schema.sql` (or split into a
gold_schema.sql — doesn't matter, this table is separate from the Medallion flow).

```sql
create table if not exists gold.ground_truth_anomalies (
    anomaly_id          varchar    primary key,
    anomaly_type        varchar    not null,
    patient_id          varchar    not null,
    injection_timestamp timestamp  not null,
    injection_batch_id  varchar    not null,
    details             json,
    detected_by_agent   boolean    default false,
    detected_at         timestamp
);
```

**anomaly_type enum (enforced in application layer, not DB):**
- `warfarin_antiplatelet_no_monitoring`
- `heart_failure_early_readmission`
- `chronic_drug_persistence_gap`
- `post_discharge_no_fill`

**Do NOT populate this table on Day 19.** Population is Day 20.

---

## Day 20 execution plan

1. Verify all four baseline counts pass sanity thresholds (<500 for gaps and
   no-fills — else refine criteria).
2. Create `gold.ground_truth_anomalies` table.
3. Implement injectors in `data_generation/anomaly_injector.py`:
   - One function per anomaly type.
   - All injections use single `injection_batch_id = uuid + timestamp`.
   - Injections write to silver_* tables AND to ground_truth_anomalies in the
     same transaction.
4. Rerun affected Gold models:
   - Anomalies 2 (HF readmission): `dbt run --select gold_readmissions`
   - Anomaly 3 (persistence gap): `dbt run --select gold_medication_adherence`
   - Others don't affect existing Gold tables.
5. Run detection queries. Confirm post-injection counts ≈ baseline + injected.
6. Add validate_gold assertions locking in post-injection counts.