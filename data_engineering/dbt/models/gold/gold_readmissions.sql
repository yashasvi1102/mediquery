{{ config(materialized='table') }}

-- Gold: 30-day hospital readmissions (CMS Hospital-Wide Readmission definition)
--
-- A readmission pair = an inpatient encounter that starts strictly AFTER a
-- prior inpatient discharge for the same patient, within 365 days.
--
-- Design notes:
--   * Overlapping encounters (readmission_start < index_discharge) are EXCLUDED.
--   * Pairs > 365 days apart are excluded.
--   * Same-day readmissions (days_between = 0) are INCLUDED and flagged.
--   * Reason codes are classified via SNOMED-suffix (DD-001 pattern).
--   * is_likely_planned filters out oncology staging + chemo admissions
--     that Synthea codes as inpatient (Synthea-specific approximation of
--     CMS Planned Readmission Algorithm v4.0).

with inpatient_encounters as (

    select
        encounter_id,
        patient_id,
        provider_id,
        start_time,
        end_time,
        length_of_stay_days,
        reason_code,
        reason_display
    from {{ ref('silver_encounters') }}
    where is_inpatient = true

),

paired as (

    select
        patient_id,
        encounter_id                        as readmission_encounter_id,
        start_time                          as readmission_start,
        reason_code                         as readmission_reason_code,
        reason_display                      as readmission_reason_display,
        provider_id                         as readmission_provider_id,
        lag(encounter_id)        over w     as index_encounter_id,
        lag(end_time)            over w     as index_discharge,
        lag(reason_code)         over w     as index_reason_code,
        lag(reason_display)      over w     as index_reason_display,
        lag(length_of_stay_days) over w     as index_length_of_stay_days
    from inpatient_encounters
    window w as (partition by patient_id order by start_time)

)

select
    patient_id,
    index_encounter_id,
    index_discharge,
    index_reason_code,
    index_reason_display,
    index_length_of_stay_days,
    readmission_encounter_id,
    readmission_start,
    readmission_reason_code,
    readmission_reason_display,
    readmission_provider_id,
    datediff('day', index_discharge, readmission_start) as days_between,
    case when datediff('day', index_discharge, readmission_start) = 0
         then true else false end                       as is_same_day,
    case when datediff('day', index_discharge, readmission_start) <= 30
         then true else false end                       as is_30_day_readmission,
    case when index_reason_code = readmission_reason_code
         then true else false end                       as is_same_reason,
    case
        when readmission_reason_display ilike '%(disorder)%'       then 'disorder'
        when readmission_reason_display ilike '%(finding)%'        then 'finding'
        when readmission_reason_display ilike '%(situation)%'      then 'situation'
        when readmission_reason_display ilike '%(procedure)%'      then 'procedure'
        when readmission_reason_display ilike '%(regime/therapy)%' then 'therapy'
        when readmission_reason_display ilike '%history of%'       then 'history'
        else 'other'
    end                                                  as readmission_reason_category,
    case
        when readmission_reason_display ilike '%(disorder)%' then true
        when readmission_reason_display ilike '%(finding)%'  then true
        else false
    end                                                  as readmission_reason_is_clinical,
    case
        when readmission_reason_display ilike '%TNM stage%'        then true
        when readmission_reason_display ilike '%chemotherapy%'     then true
        when readmission_reason_display ilike '%(regime/therapy)%' then true
        when readmission_reason_display ilike '%malignant neoplasm%' then true
        else false
    end                                                  as is_likely_planned
from paired
where index_encounter_id is not null
  and datediff('day', index_discharge, readmission_start) >= 0
  and datediff('day', index_discharge, readmission_start) <= 365