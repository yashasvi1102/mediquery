{{ config(materialized='table') }}

with deduped as (
    select
        *,
        row_number() over (
            partition by medication_request_id
            order by load_timestamp desc
        ) as rn
    from {{ source('bronze', 'bronze_medication_requests') }}
),

latest as (
    select * from deduped where rn = 1
),

classified as (
    select
        medication_request_id,
        patient_id,
        encounter_id,

        status,
        intent,

        code_system,
        medication_code as rxnorm_code,
        medication_display,

        -- medication_flag: pairs with silver_conditions.condition_flag for
        -- Day 17 medication adherence analysis. Insulin included alongside
        -- metformin for diabetes because Synthea prescribes insulin to most
        -- T2DM patients; excluding it undercounted the cohort 3x.
        -- Antihypertensive is broad (any first-line drug) since HTN treatment
        -- is heterogeneous in Synthea.
        case
            when lower(medication_display) like '%metformin%'
              or lower(medication_display) like '%insulin%'             then 'diabetes_drug'
            when lower(medication_display) like '%lisinopril%'
              or lower(medication_display) like '%amlodipine%'
              or lower(medication_display) like '%hydrochlorothiazide%'
              or lower(medication_display) like '%losartan%'            then 'antihypertensive'
            when lower(medication_display) like '%furosemide%'
              or lower(medication_display) like '%carvedilol%'          then 'heart_failure_drug'
            when lower(medication_display) like '%albuterol%'
              or lower(medication_display) like '%fluticasone%'         then 'copd_drug'
            else null
        end as medication_flag,

        -- is_active: still being prescribed (vs completed course)
        (lower(status) = 'active') as is_active,

        -- drug_class: therapeutic class for analytics. Granular on purpose
        -- (biguanide and insulin stay separate) so downstream queries can
        -- distinguish T2DM vs T1DM treatment patterns.
        case
            when lower(medication_display) like '%metformin%'           then 'biguanide'
            when lower(medication_display) like '%insulin%'             then 'insulin'
            when lower(medication_display) like '%lisinopril%'          then 'ace_inhibitor'
            when lower(medication_display) like '%losartan%'            then 'arb'
            when lower(medication_display) like '%amlodipine%'          then 'calcium_channel_blocker'
            when lower(medication_display) like '%hydrochlorothiazide%' then 'thiazide_diuretic'
            when lower(medication_display) like '%furosemide%'          then 'loop_diuretic'
            when lower(medication_display) like '%carvedilol%'          then 'beta_blocker'
            when lower(medication_display) like '%simvastatin%'
              or lower(medication_display) like '%atorvastatin%'        then 'statin'
            when lower(medication_display) like '%albuterol%'           then 'bronchodilator'
            when lower(medication_display) like '%fluticasone%'         then 'inhaled_corticosteroid'
            when lower(medication_display) like '%acetaminophen%'       then 'analgesic'
            when lower(medication_display) like '%ibuprofen%'           then 'nsaid'
            when lower(medication_display) like '%hydrocodone%'
              or lower(medication_display) like '%oxycodone%'
              or lower(medication_display) like '%fentanyl%'            then 'opioid'
            else 'other'
        end as drug_class,

        authored_on,
        dosage_text,

        load_timestamp,
        load_batch_id

    from latest
)

select * from classified