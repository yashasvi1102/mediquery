{{ config(materialized='table') }}

-- Gold: Chronic conditions cohort
--
-- Grain: one row per (patient_id, condition_flag) pair. A patient with
-- diabetes_t2 and hypertension produces 2 rows. Downstream cohort
-- filters use `where condition_flag = 'diabetes_t2'`.
--
-- Design notes:
--   * Only patients with condition_flag IS NOT NULL are included. This
--     ships DD-001 forward: naive "patients with conditions" queries
--     inflate cohorts 3x because 67% of Silver conditions are social
--     factors or admin events.
--   * A single chronic condition can span many rows in Silver (T2DM
--     with complications = 2.4 rows/patient per Day 10). We collapse
--     to one row per patient-condition using MIN(onset_date).
--   * age_years_current is non-deterministic (Day 9 note). Fine for
--     cohort filters. Time-based analysis must recompute from birth_date.

with chronic_diagnoses as (

    select
        patient_id,
        condition_flag,
        min(onset_date) as first_onset_date,
        max(onset_date) as latest_onset_date,
        count(*)        as diagnosis_row_count
    from {{ ref('silver_conditions') }}
    where condition_flag is not null
    group by patient_id, condition_flag

),

patient_context as (

    select
        patient_id,
        gender,
        birth_date,
        age_years_current,
        age_group,
        is_deceased,
        deceased_date
    from {{ ref('silver_patients') }}

),

comorbidity_counts as (

    select
        patient_id,
        count(distinct condition_flag) as comorbidity_count
    from chronic_diagnoses
    group by patient_id

)

select
    cd.patient_id,
    cd.condition_flag,
    cd.first_onset_date,
    cd.latest_onset_date,
    cd.diagnosis_row_count,
    pc.gender,
    pc.age_years_current,
    pc.age_group,
    pc.is_deceased,
    datediff('year', cd.first_onset_date, pc.birth_date) * -1        as age_at_first_diagnosis,
    datediff('year', cd.first_onset_date,
             coalesce(pc.deceased_date, current_timestamp))          as years_since_first_diagnosis,
    cc.comorbidity_count,
    case when cc.comorbidity_count >= 2 then true else false end     as has_comorbidities
from chronic_diagnoses cd
inner join patient_context     pc using (patient_id)
inner join comorbidity_counts  cc using (patient_id)