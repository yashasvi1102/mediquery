{{ config(materialized='table') }}

-- Gold: Per-provider volume metrics.
--
-- Grain: one row per provider_id. Every provider that appears in
-- silver_encounters gets a row. Providers with zero encounters are absent
-- (Synthea doesn't expose a Provider directory as a separate resource in
-- this pipeline — providers are only known via their encounter appearances).

with encounters as (

    select
        provider_id,
        patient_id,
        encounter_id,
        encounter_type,
        is_inpatient,
        length_of_stay_days,
        duration_minutes,
        start_time
    from {{ ref('silver_encounters') }}
    where provider_id is not null

),

top_condition_per_provider as (

    -- Most common clinical condition category treated by each provider.
    -- Uses is_billable_diagnosis to skip Synthea's non-clinical findings
    -- (DD-001). Ties broken alphabetically by clinical_category.
    select
        e.provider_id,
        sc.clinical_category                              as top_clinical_category,
        row_number() over (
            partition by e.provider_id
            order by count(*) desc, sc.clinical_category
        )                                                 as rn
    from encounters e
    inner join {{ ref('silver_conditions') }} sc
        on e.encounter_id = sc.encounter_id
    where sc.is_billable_diagnosis = true
    group by e.provider_id, sc.clinical_category

),

per_provider as (

    select
        provider_id,
        count(*)                                                            as total_encounters,
        count(distinct patient_id)                                          as unique_patients,
        count(*) filter (where encounter_type = 'ambulatory')                as ambulatory_encounters,
        count(*) filter (where encounter_type = 'emergency')                 as emergency_encounters,
        count(*) filter (where encounter_type = 'inpatient')                 as inpatient_encounters,
        count(*) filter (where encounter_type = 'home_health')               as home_health_encounters,
        count(*) filter (where encounter_type = 'virtual')                   as virtual_encounters,
        coalesce(sum(coalesce(length_of_stay_days, 0)) filter (where is_inpatient), 0) as total_inpatient_days,
        round(avg(duration_minutes), 1)                                     as avg_encounter_duration_minutes,
        min(start_time)                                                     as first_encounter_date,
        max(start_time)                                                     as last_encounter_date
    from encounters
    group by provider_id

)

select
    pp.provider_id,
    pp.total_encounters,
    pp.unique_patients,
    round(1.0 * pp.total_encounters / pp.unique_patients, 2)      as avg_encounters_per_patient,
    pp.ambulatory_encounters,
    pp.emergency_encounters,
    pp.inpatient_encounters,
    pp.home_health_encounters,
    pp.virtual_encounters,
    pp.total_inpatient_days,
    pp.avg_encounter_duration_minutes,
    pp.first_encounter_date,
    pp.last_encounter_date,
    datediff('day', pp.first_encounter_date, pp.last_encounter_date) as active_days,
    tc.top_clinical_category
from per_provider pp
left join top_condition_per_provider tc
    on pp.provider_id = tc.provider_id and tc.rn = 1