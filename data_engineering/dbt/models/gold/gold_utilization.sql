{{ config(materialized='table') }}

-- Gold: Per-patient utilization metrics.
--
-- Grain: one row per patient_id. Every patient in silver_patients gets a row,
-- even if they have zero encounters (LEFT JOIN preserves them). Downstream
-- reporting uses this as the "who used the system, how much" summary.
--
-- Cost is not modeled — Synthea generates fabricated fixed-rate costs that
-- don't reflect real payer contracts. Length of stay and duration are used
-- as utilization proxies instead. See LEARNINGS Day 18.

with encounters as (

    select
        patient_id,
        encounter_id,
        encounter_type,
        is_inpatient,
        duration_minutes,
        length_of_stay_days,
        start_time,
        end_time
    from {{ ref('silver_encounters') }}

),

per_patient as (

    select
        patient_id,
        count(*)                                                        as total_encounters,
        count(*) filter (where encounter_type = 'ambulatory')            as ambulatory_encounters,
        count(*) filter (where encounter_type = 'emergency')             as emergency_encounters,
        count(*) filter (where encounter_type = 'inpatient')             as inpatient_encounters,
        count(*) filter (where encounter_type = 'home_health')           as home_health_encounters,
        count(*) filter (where encounter_type = 'virtual')               as virtual_encounters,
        sum(coalesce(length_of_stay_days, 0)) filter (where is_inpatient) as total_inpatient_days,
        round(avg(duration_minutes), 1)                                 as avg_encounter_duration_minutes,
        min(start_time)                                                 as first_encounter_date,
        max(start_time)                                                 as last_encounter_date,
        datediff('day', min(start_time), max(start_time))               as active_days
    from encounters
    group by patient_id

)

select
    sp.patient_id,
    coalesce(pp.total_encounters, 0)              as total_encounters,
    coalesce(pp.ambulatory_encounters, 0)         as ambulatory_encounters,
    coalesce(pp.emergency_encounters, 0)          as emergency_encounters,
    coalesce(pp.inpatient_encounters, 0)          as inpatient_encounters,
    coalesce(pp.home_health_encounters, 0)        as home_health_encounters,
    coalesce(pp.virtual_encounters, 0)            as virtual_encounters,
    coalesce(pp.total_inpatient_days, 0)          as total_inpatient_days,
    pp.avg_encounter_duration_minutes,
    pp.first_encounter_date,
    pp.last_encounter_date,
    coalesce(pp.active_days, 0)                   as active_days,
    case when pp.total_encounters is null then true else false end as has_no_encounters
from {{ ref('silver_patients') }} sp
left join per_patient pp using (patient_id)