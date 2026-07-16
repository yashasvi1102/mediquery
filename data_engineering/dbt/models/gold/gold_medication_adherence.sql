{{ config(materialized='table') }}

-- Gold: Medication adherence via approximated PDC (Proportion of Days Covered).
--
-- DD-002 pivot: clinical outcomes unreliable on Synthea; use prescription-pattern.
--
-- DD-004: PDC is measured over a bounded 365-day window ending at last_prescription
-- (CMS methodology). Synthea generates lifetime histories, so a naive
-- "lifetime coverage" ratio would penalize long-lived patients with any gap.
-- lifetime_coverage_ratio is retained for comparison.
--
-- METHODOLOGY:
--   * PDC window = 365 days ending at last_prescription.
--   * days_covered = count of fills within window * assumed_days_supply,
--     capped at 365.
--   * Adherent = pdc >= 0.80 (CMS threshold).
--   * assumed_days_supply is class-level (90d oral maintenance, 30d inhalers,
--     insulin, anticoagulants, opioids) — Synthea emits no dispensing quantity.
--
-- GRAIN: one row per (patient_id, drug_class).

with chronic_prescriptions as (

    select
        patient_id,
        drug_class,
        medication_flag,
        authored_on,
        case drug_class
            when 'biguanide'               then 90
            when 'sulfonylurea'            then 90
            when 'insulin'                 then 30
            when 'ace_inhibitor'           then 90
            when 'arb'                     then 90
            when 'beta_blocker'            then 90
            when 'calcium_channel_blocker' then 90
            when 'diuretic'                then 90
            when 'loop_diuretic'           then 30
            when 'statin'                  then 90
            when 'bronchodilator'          then 30
            when 'corticosteroid_inhaled'  then 30
            when 'anticoagulant'           then 30
            when 'opioid'                  then 30
            else 30
        end as assumed_days_supply
    from {{ ref('silver_medications') }}
    where medication_flag is not null
      and status in ('active', 'completed')

),

patient_class_bounds as (

    select
        patient_id,
        drug_class,
        min(medication_flag)                    as medication_flag,
        min(authored_on)                        as first_prescription,
        max(authored_on)                        as last_prescription,
        max(assumed_days_supply)                as assumed_days_supply,
        count(*)                                as prescription_count,
        sum(assumed_days_supply)                as lifetime_days_supplied
    from chronic_prescriptions
    group by patient_id, drug_class

),

-- Restrict fills to the CMS 365-day window ending at last_prescription.
windowed_fills as (

    select
        cp.patient_id,
        cp.drug_class,
        count(*)                                as fills_in_window,
        sum(cp.assumed_days_supply)             as raw_days_covered
    from chronic_prescriptions cp
    inner join patient_class_bounds pcb using (patient_id, drug_class)
    where cp.authored_on >= (pcb.last_prescription - interval '365 days')
    group by cp.patient_id, cp.drug_class

),

patient_endpoints as (

    select
        patient_id,
        deceased_date,
        coalesce(deceased_date, current_timestamp) as observation_end
    from {{ ref('silver_patients') }}

)

select
    pcb.patient_id,
    pcb.drug_class,
    pcb.medication_flag,
    pcb.first_prescription,
    pcb.last_prescription,
    pcb.prescription_count,
    pcb.assumed_days_supply,
    pcb.lifetime_days_supplied,
    -- CMS 365-day window
    365                                          as measurement_period_days,
    wf.fills_in_window,
    least(wf.raw_days_covered, 365)              as days_covered,
    round(1.0 * least(wf.raw_days_covered, 365) / 365, 3) as pdc,
    -- Full-history reference metric (not PDC-standard)
    datediff('day', pcb.first_prescription, pe.observation_end) as lifetime_period_days,
    case
        when datediff('day', pcb.first_prescription, pe.observation_end) > 0
        then round(
            1.0 * least(pcb.lifetime_days_supplied, datediff('day', pcb.first_prescription, pe.observation_end))
            / datediff('day', pcb.first_prescription, pe.observation_end), 3)
        else null
    end as lifetime_coverage_ratio,
    -- Persistence: days from first to last prescription
    datediff('day', pcb.first_prescription, pcb.last_prescription) as persistence_days,
    case
        when pcb.prescription_count = 1                                          then 'single_fill'
        when 1.0 * least(wf.raw_days_covered, 365) / 365 >= 0.80                 then 'adherent'
        when 1.0 * least(wf.raw_days_covered, 365) / 365 >= 0.50                 then 'partial'
        else 'non_adherent'
    end as adherence_class
from patient_class_bounds pcb
inner join windowed_fills   wf using (patient_id, drug_class)
inner join patient_endpoints pe using (patient_id)