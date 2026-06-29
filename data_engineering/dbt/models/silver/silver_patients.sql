{{ config(materialized='table') }}

with deduped as (
    select
        *,
        row_number() over (
            partition by patient_id
            order by load_timestamp desc
        ) as rn
    from {{ source('bronze', 'bronze_patients') }}
),

latest as (
    select * from deduped where rn = 1
),

final as (
    select
        patient_id,

        -- name
        given_name,
        family_name,

        -- demographics (FHIR-native values, lowercased for safety)
        lower(gender) as gender,
        birth_date,
        deceased_date,
        (deceased_date is not null) as is_deceased,

        -- age (current as helper; gold computes age-at-encounter from birth_date)
        date_diff('year', birth_date, current_date) as age_years_current,
        case
            when deceased_date is not null
            then date_diff('year', birth_date, deceased_date::date)
        end as age_at_death,

        -- age buckets for cohort filters
        case
            when date_diff('year', birth_date, current_date) < 18 then 'pediatric'
            when date_diff('year', birth_date, current_date) < 40 then 'adult_young'
            when date_diff('year', birth_date, current_date) < 65 then 'adult_middle'
            else 'senior'
        end as age_group,

        -- demographics from US Core
        marital_status,
        race,
        ethnicity,

        -- address (already structured in bronze)
        city,
        state,
        postal_code,
        country,

        -- audit
        load_timestamp,
        load_batch_id

    from latest
)

select * from final