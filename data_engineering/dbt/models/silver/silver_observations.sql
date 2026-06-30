{{ config(materialized='table') }}

with deduped as (
    select
        *,
        row_number() over (
            partition by observation_id
            order by load_timestamp desc
        ) as rn
    from {{ source('bronze', 'bronze_observations') }}
),

latest as (
    select * from deduped where rn = 1
),

classified as (
    select
        observation_id,
        parent_observation_id,
        patient_id,
        encounter_id,

        status,
        category,

        loinc_code,
        loinc_display,

        value_numeric,
        value_text,
        value_code,
        value_code_system,
        unit,

        -- observation_kind: high-level bucket for Gold/anomaly queries.
        -- Pairs with condition_flag and medication_flag for chronic-disease
        -- monitoring (Day 17 adherence checks HbA1c; Day 19 anomaly checks
        -- critical labs).
        case
            when loinc_code = '4548-4'                            then 'hba1c'
            when loinc_code = '2339-0'                            then 'glucose_blood'
            when loinc_code in ('8480-6','8462-4')                then 'blood_pressure'
            when loinc_code = '8867-4'                            then 'heart_rate'
            when loinc_code = '9279-1'                            then 'respiratory_rate'
            when loinc_code = '8302-2'                            then 'height'
            when loinc_code = '29463-7'                           then 'weight'
            when loinc_code = '39156-5'                           then 'bmi'
            when loinc_code = '33914-3'                           then 'egfr_kidney'
            when loinc_code = '2160-0'                            then 'creatinine'
            when loinc_code in ('2093-3','2085-9','2089-1','13457-7') then 'cholesterol_panel'
            when loinc_code = '72166-2'                           then 'smoking_status'
            when loinc_code = '72514-3'                           then 'pain_score'
            when category = 'social-history'                      then 'social_history'
            when category = 'survey'                              then 'survey'
            when category = 'laboratory'                          then 'lab_other'
            when category = 'vital-signs'                         then 'vital_other'
            else 'other'
        end as observation_kind,

      -- is_critical_value: clinically actionable threshold flags. Used by
        -- Day 19 anomaly detection. Conservative thresholds; clinical judgment
        -- would tighten these per-patient.
        case
            when loinc_code = '4548-4' and value_numeric >= 9      then true   -- HbA1c >= 9% (severe diabetes)
            when loinc_code = '2339-0' and value_numeric >= 400    then true   -- glucose >= 400 mg/dL
            when loinc_code = '2339-0' and value_numeric <= 40     then true   -- glucose <= 40 mg/dL (severe hypo)
            when loinc_code = '8480-6' and value_numeric >= 180    then true   -- systolic >= 180 (hypertensive crisis)
            when loinc_code = '8480-6' and value_numeric <= 80     then true   -- systolic <= 80 (hypotension)
            when loinc_code = '8462-4' and value_numeric >= 120    then true   -- diastolic >= 120
            when loinc_code = '8867-4' and value_numeric >= 130    then true   -- HR >= 130 (severe tachycardia)
            when loinc_code = '8867-4' and value_numeric <= 40     then true   -- HR <= 40 (severe bradycardia)
            when loinc_code = '33914-3' and value_numeric < 30     then true   -- eGFR < 30 (stage 4 CKD)
            else false
        end as is_critical_value,

        -- is_plausible_value: clinical-range sanity check. Synthea generates
        -- values outside biologically possible ranges, notably HbA1c (~49% of
        -- readings are < 4%, which is incompatible with life). Gold/anomaly
        -- models should filter on this column for clinically meaningful analytics.
        -- Non-numeric observations default to true (no range to check).
        case
            -- HbA1c: human range 4.0-15.0%
            when loinc_code = '4548-4' and value_numeric < 4.0   then false
            when loinc_code = '4548-4' and value_numeric > 15.0  then false
            -- Systolic BP: 60-260 mm Hg
            when loinc_code = '8480-6' and value_numeric < 60    then false
            when loinc_code = '8480-6' and value_numeric > 260   then false
            -- Diastolic BP: 30-150 mm Hg
            when loinc_code = '8462-4' and value_numeric < 30    then false
            when loinc_code = '8462-4' and value_numeric > 150   then false
            -- Heart rate: 30-220 bpm
            when loinc_code = '8867-4' and value_numeric < 30    then false
            when loinc_code = '8867-4' and value_numeric > 220   then false
            -- Glucose: 20-800 mg/dL
            when loinc_code = '2339-0' and value_numeric < 20    then false
            when loinc_code = '2339-0' and value_numeric > 800   then false
            else true
        end as is_plausible_value,

        -- is_numeric: convenient filter for analytics that only want numerics
        (value_numeric is not null) as is_numeric,

        effective_date,
        issued_date,

        load_timestamp,
        load_batch_id

    from latest
)

select * from classified