{{ config(materialized='table') }}

with deduped as (
    select
        *,
        row_number() over (
            partition by condition_id
            order by load_timestamp desc
        ) as rn
    from {{ source('bronze', 'bronze_conditions') }}
),

latest as (
    select * from deduped where rn = 1
),

classified as (
    select
        condition_id,
        patient_id,
        encounter_id,

        code as snomed_code,
        display,

        -- DD-001: extract trailing parenthetical from SNOMED display string.
        -- Pattern: "Diabetes mellitus type 2 (disorder)" -> "disorder"
        -- Maps morphologic abnormality (sprains, burns) -> disorder.
        -- Maps person (refugee, etc.) -> finding (demographic descriptor).
        -- ~0.15% of rows have no SNOMED suffix and default to 'unknown'.
        case
            when display ilike '%(disorder)'                then 'disorder'
            when display ilike '%(finding)'                 then 'finding'
            when display ilike '%(situation)'               then 'situation'
            when display ilike '%(procedure)'               then 'procedure'
            when display ilike '%(event)'                   then 'event'
            when display ilike '%(morphologic abnormality)' then 'disorder'
            when display ilike '%(person)'                  then 'finding'
            else 'unknown'
        end as clinical_category,

        -- condition_flag: NULL for ~99% of rows. Set only for the 4 chronic
        -- conditions that downstream Gold/anomaly models reference.
        -- Centralizing the SNOMED code lists here means Gold queries filter
        -- by flag, not by hardcoded code lists scattered across models.
        case
            -- Diabetes T2: parent disorder + known T2DM complications.
            -- Without complications, Day 16 chronic-condition cohort undercounts.
            when code in (
                '44054006',         -- Diabetes mellitus type 2
                '127013003',        -- Disorder of kidney due to diabetes
                '90781000119102',   -- Microalbuminuria due to T2DM
                '157141000119108',  -- Proteinuria due to T2DM
                '368581000119106',  -- Neuropathy due to T2DM
                '97331000119101',   -- Macular edema and retinopathy due to T2DM
                '60951000119105',   -- Blindness due to T2DM
                '1551000119108',    -- Nonproliferative diabetic retinopathy
                '1501000119109'     -- Proliferative diabetic retinopathy
            ) then 'diabetes_t2'

            when code in (
                '88805009',         -- Chronic congestive heart failure
                '84114007'          -- Heart failure
            ) then 'heart_failure'

            when code = '59621000'  -- Essential hypertension
                then 'hypertension'

            when code = '87433001'  -- Pulmonary emphysema (COPD proxy)
                then 'copd'

            else null
        end as condition_flag,

        -- is_billable_diagnosis: any clinical disorder a hospital would bill
        -- for. Mirrors clinical_category = 'disorder' (including morphologic
        -- abnormalities like sprains and burns). Excludes findings, situations,
        -- and unknowns. If you add a disorder-like suffix above, update here too.
        (
            display ilike '%(disorder)'
            or display ilike '%(morphologic abnormality)'
        ) as is_billable_diagnosis,

        clinical_status,
        verification_status,

        onset_date,
        abatement_date,
        recorded_date,

        -- is_active: condition has been recorded and not yet abated.
        -- Defaults clinical_status to 'active' when null (Synthea is sparse here).
        (
            abatement_date is null
            and lower(coalesce(clinical_status, 'active')) in ('active', 'recurrence', 'relapse')
        ) as is_active,

        -- audit
        load_timestamp,
        load_batch_id

    from latest
)

select * from classified