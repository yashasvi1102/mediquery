{{ config(materialized='table') }}

with deduped as (
    select
        *,
        row_number() over (
            partition by encounter_id
            order by load_timestamp desc
        ) as rn
    from {{ source('bronze', 'bronze_encounters') }}
),

latest as (
    select * from deduped where rn = 1
),

final as (
    select
        encounter_id,
        patient_id,
        provider_id,

        status,

        -- FHIR class code -> readable encounter type
        class_code,
        case class_code
            when 'AMB'  then 'ambulatory'
            when 'EMER' then 'emergency'
            when 'IMP'  then 'inpatient'
            when 'HH'   then 'home_health'
            when 'VR'   then 'virtual'
            else 'other'
        end as encounter_type,

        -- inpatient flag for Day 15 readmissions
        (class_code = 'IMP') as is_inpatient,

        type_code,
        type_display,
        reason_code,
        reason_display,

        start_time,
        end_time,

        -- duration helpers
        date_diff('minute', start_time, end_time) as duration_minutes,
        case
            when end_time is null or start_time is null then null
            else date_diff('day', start_time::date, end_time::date)
        end as length_of_stay_days,

        -- audit
        load_timestamp,
        load_batch_id

    from latest
)

select * from final