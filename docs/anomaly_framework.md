# Anomaly Injection Framework

## Purpose

The MediQuery AI agent (Week 5) is evaluated on its ability to detect clinical
anomalies in patient data. Real anomaly detection benchmarks require ground
truth — a known set of anomalies with known counts. This framework:

1. Defines two clinically-motivated anomaly types.
2. Injects known instances into Silver-layer tables with recorded ground truth.
3. Measures baseline false-positive counts pre-injection so precision/recall
   post-injection is meaningful, not measured against noise.

## Scope decisions

The original Day 19 plan called for 4 anomaly types. After baseline analysis,
2 were dropped per DD-005 — Synthea's encounter-tied prescription model
(DD-004) generates too many natural false positives for adherence-style
anomalies to produce measurable signal.

| Anomaly | Status | Reason |
|---|---|---|
| Missed annual screening for diabetics | Dropped (Day 12) | DD-002: Synthea observation values not clinically valid |
| Critical lab values without follow-up | Dropped (Day 12) | Same |
| Chronic-drug persistence gap | Dropped (Day 19) | DD-005: baseline 2,433 vs 40 injected = 1:60 signal:noise |
| Post-discharge medication non-fill | Dropped (Day 19) | DD-005: baseline 470 vs 25 injected = 1:19 signal:noise |
| Warfarin + NSAID/aspirin coprescription | Ship | Baseline 11, target 30 |
| Heart failure 7-day readmission | Ship | Baseline 5, target 25 |

Two anomaly types with 3-5x injected:baseline ratio is a stronger benchmark
than four with poor signal:noise. See DD-005 for the full analysis.

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

**Volume.** Small enough to stay clinically plausible (real hospital adverse
event rates are 3-10% depending on category), large enough for statistical
significance.

---

## Anomaly Type 1: Warfarin-NSAID/Aspirin Coprescription

### Clinical significance

Concurrent warfarin + aspirin or NSAID doubles-to-triples major bleeding risk.
Standard of care: INR monitoring every 2-4 weeks during co-prescription.
Real EHRs flag this via drug-drug interaction alerts. Studies estimate
15-30% of warfarin patients have inappropriate antiplatelet coprescription
in outpatient settings.

### Detection criterion

Patient has:
- Warfarin prescription (`medication_display ilike '%warfarin%'`)
- Overlapping aspirin or NSAID prescription authored within 30 days of warfarin
  (either direction)

INR observation check is omitted from the shipped detection query because
Synthea's observation model is unreliable (DD-002). The clinical criterion
would include INR monitoring; the coded criterion does not.

### Data-model note

Synthea's silver_medications.drug_class taxonomy does not include
`anticoagulant` or `antiplatelet` classes. Warfarin sits under `other`;
aspirin also sits under `other`; NSAIDs are under `nsaid`. Detection uses
`medication_display ilike` for warfarin and aspirin, `drug_class = 'nsaid'`
for NSAIDs. This is a silver_medications taxonomy gap (see LEARNINGS Day 21
cleanup notes), not an anomaly-framework problem.

### Injection strategy

- **Target patients:** 30 patients aged ≥55 with no existing warfarin
  prescription and no existing aspirin/NSAID prescription in silver.
- **Rows inserted per patient:**
  - `silver_medications` row: warfarin sodium 5 MG oral tablet,
    RxNorm sampled from existing warfarin rows, drug_class = 'other',
    authored_on = random date in 2015-2020, status = 'active'
  - `silver_medications` row: aspirin 81 MG oral capsule or ibuprofen 400 MG
    oral tablet, drug_class matches existing rows (`other` or `nsaid`),
    authored_on = warfarin_date + random 3-14 days
- Both rows reference a real provider_id and encounter_id from that patient's
  existing history.

### Ground truth

30 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'warfarin_antiplatelet_no_monitoring'`. Details JSON includes
both medication_request_ids, the drug pair, and the overlap start/end dates.

### Baseline

**Baseline count:** 11 patients (measured Day 19)
**Target injected:** 30
**Expected post-injection:** 41 total detections

### Detection query

```sql
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
select distinct patient_id from concurrent_nsaid_aspirin;
```

---

## Anomaly Type 2: Heart Failure 7-Day Readmission

### Clinical significance

CMS penalizes hospitals for HF readmissions within 30 days. 7-day readmissions
signal severe issues — inadequate discharge planning, unresolved decompensation,
or medication non-reconciliation. Real HF 30-day readmission rate is ~20%;
7-day rate is ~5%.

### Detection criterion

Patient has two inpatient encounters where:
- Index encounter reason mentions "heart failure" or "congestive"
- Readmission encounter starts ≤ 7 days after index discharge
- Readmission is not a planned procedure (`is_likely_planned = false`)

Reuses gold_readmissions filtering logic — the DD-003 clinical/unplanned
filters already applied.

### Injection strategy

- **Target patients:** 25 patients with `condition_flag = 'heart_failure'`
  in gold_chronic_conditions who have exactly ONE prior inpatient encounter
  with HF as reason.
- **Rows inserted per patient:**
  - `silver_encounters` row: inpatient, `class_code = 'IMP'`,
    `is_inpatient = true`, `encounter_type = 'inpatient'`
  - `reason_display = 'Chronic congestive heart failure (disorder)'`,
    `reason_code = 88805009` (SNOMED for CHF)
  - `start_time` = (existing HF encounter `end_time`) + 2-6 days
  - `end_time` = `start_time` + 2-5 days
  - `provider_id` sampled from providers currently seen treating HF patients
    in silver_encounters

### Ground truth

25 patients recorded in gold.ground_truth_anomalies with
`anomaly_type = 'heart_failure_early_readmission'`. Details JSON includes
both encounter_ids and days_between.

### Baseline

**Baseline count:** 5 pairs (measured Day 19)
**Target injected:** 25
**Expected post-injection:** 30 pairs

Note: gold_readmissions is a downstream model. After injection, rerun
`dbt run --select gold_readmissions` to propagate the injected encounters
before running detection.

### Detection query

```sql
select count(*)
from gold.gold_readmissions
where is_30_day_readmission = true
  and days_between <= 7
  and is_likely_planned = false
  and (
    index_reason_display ilike '%heart failure%'
    or index_reason_display ilike '%congestive%'
  );
```

---

## Baseline Summary

| Anomaly type | Baseline | Target injected | Expected post-injection |
|---|---|---|---|
| Warfarin + NSAID/aspirin | 11 | 30 | 41 |
| HF 7-day readmission | 5 | 25 | 30 |

**Precision/recall formulas (Day 39 benchmark):**

- Recall = (agent-detected-injected) / (total-injected)
- Precision = (agent-detected-injected) / (agent-detected-total)
- Where agent-detected-total = agent-detected-injected + agent-detected-baseline

Baseline detections are NOT false positives — they're real clinical patterns
that pre-existed the injection. They cannot count toward recall (weren't
injected) but they DO reduce precision if the agent flags them.

---

## `gold.ground_truth_anomalies` schema

`data_engineering/schema/gold_schema.sql`:

```sql
create schema if not exists gold;

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

**Do NOT populate this table on Day 19.** Population is Day 20.

---

## Day 20 execution plan

1. Create `gold.ground_truth_anomalies` table via
   `data_engineering/schema/gold_schema.sql`.
2. Implement injectors in `data_generation/anomaly_injector.py`:
   - One function per anomaly type: `inject_warfarin_coprescription()`,
     `inject_hf_early_readmission()`.
   - All injections use single `injection_batch_id = uuid + timestamp`.
   - Each injection writes to `silver_*` tables AND to
     `gold.ground_truth_anomalies` in the same transaction.
3. Rerun affected Gold models:
   - HF readmission injection: `dbt run --select gold_readmissions`
   - Warfarin injection: no Gold table dependency, skip.
4. Rerun detection queries. Confirm post-injection counts:
   - Warfarin: 41 (baseline 11 + injected 30)
   - HF readmission: 30 (baseline 5 + injected 25)
5. Add validate_gold assertions locking in the post-injection counts.

---

## Dropped Anomalies (Post-Baseline Analysis)

See DD-005 for the full analysis. Not shipping.

### Dropped: Chronic-drug persistence gap
Baseline 2,433 vs target 40 = 1:60 signal:noise. Synthea's encounter-tied
prescription model (DD-004) means natural gaps between prescriptions are
the statistical norm for chronic patients, not the exception. No amount
of criterion tightening produced a usable ratio.

### Dropped: Post-discharge medication non-fill
Baseline 470 vs target 25 = 1:19 signal:noise. Same root cause as above.
Even after requiring the patient had a follow-up encounter in the 90-day
window, natural non-fills dominate injected non-fills.

Both would become detectable if Bronze were rebuilt with MedicationDispense
parsing (Week 4+ optional extension). Not doing that in this project.