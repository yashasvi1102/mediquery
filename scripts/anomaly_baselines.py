import duckdb
con = duckdb.connect('mediquery.duckdb', read_only=True)

print("=== Baseline 1: Warfarin-antiplatelet ===")
print(con.sql("""
    with warfarin_patients as (
        select distinct patient_id, min(authored_on) as first_warfarin
        from silver.silver_medications
        where drug_class = 'anticoagulant'
          and medication_display ilike '%warfarin%'
        group by patient_id
    ),
    antiplatelet_overlap as (
        select w.patient_id
        from warfarin_patients w
        inner join silver.silver_medications m
            on w.patient_id = m.patient_id
           and m.drug_class in ('antiplatelet')
           and abs(datediff('day', w.first_warfarin, m.authored_on)) <= 30
    )
    select count(distinct patient_id) as baseline_count from antiplatelet_overlap;
""").fetchdf())

print("\n=== Baseline 2: HF 7-day readmission ===")
print(con.sql("""
    select count(*) as baseline_count
    from gold.gold_readmissions
    where is_30_day_readmission = true
      and days_between <= 7
      and is_likely_planned = false
      and (
        index_reason_display ilike '%heart failure%'
        or index_reason_display ilike '%congestive%'
      );
""").fetchdf())

print("\n=== Baseline 3: Chronic-drug persistence gap ===")
print(con.sql("""
    with last_fills as (
        select
            patient_id,
            drug_class,
            max(authored_on) as last_prescription,
            count(*)         as n_prescriptions
        from silver.silver_medications
        where medication_flag is not null
        group by patient_id, drug_class
        having count(*) >= 3
    )
    select count(*) as baseline_count
    from last_fills
    where datediff('day', last_prescription, current_timestamp) >= 180
      and datediff('day', last_prescription, current_timestamp) <= 3650;
""").fetchdf())

print("\n=== Baseline 4: Post-discharge no fill ===")
print(con.sql("""
    with discharge_prescriptions as (
        select
            e.patient_id,
            e.encounter_id,
            e.end_time as discharge_date,
            m.drug_class,
            m.authored_on
        from silver.silver_encounters e
        inner join silver.silver_medications m
            on e.encounter_id = m.encounter_id
        where e.is_inpatient = true
          and m.medication_flag is not null
    ),
    followup_fills as (
        select
            dp.patient_id,
            dp.discharge_date,
            dp.drug_class,
            count(m2.medication_request_id) as followup_count
        from discharge_prescriptions dp
        left join silver.silver_medications m2
            on dp.patient_id = m2.patient_id
           and m2.drug_class = dp.drug_class
           and m2.authored_on > dp.discharge_date
           and m2.authored_on <= dp.discharge_date + interval '90 days'
        group by dp.patient_id, dp.discharge_date, dp.drug_class
    )
    select count(*) as baseline_count
    from followup_fills
    where followup_count = 0;
""").fetchdf())
print("\n=== Aspirin/ibuprofen/naproxen breakdown ===")
print(con.sql("""
    select medication_display, drug_class, count(*) as n
    from silver.silver_medications
    where medication_display ilike '%aspirin%'
       or medication_display ilike '%ibuprofen%'
       or medication_display ilike '%naproxen%'
    group by medication_display, drug_class
    order by n desc
    limit 15
""").fetchdf())

print("\n=== Baseline 1 REVISED: Warfarin + NSAID/aspirin coprescription ===")
print(con.sql("""
    with warfarin_patients as (
        select
            patient_id,
            authored_on as warfarin_start
        from silver.silver_medications
        where medication_display ilike '%warfarin%'
    ),
    concurrent_nsaid_aspirin as (
        select
            w.patient_id,
            w.warfarin_start,
            m.authored_on as nsaid_start,
            m.medication_display
        from warfarin_patients w
        inner join silver.silver_medications m
            on w.patient_id = m.patient_id
           and (
                m.medication_display ilike '%aspirin%'
                or m.drug_class = 'nsaid'
           )
           and abs(datediff('day', w.warfarin_start, m.authored_on)) <= 30
    )
    select count(distinct patient_id) as baseline_count
    from concurrent_nsaid_aspirin
""").fetchdf())

print("\n=== Baseline 3 REFINED: persistence gap + still-engaged ===")
print(con.sql("""
    with last_fills as (
        select
            patient_id,
            drug_class,
            max(authored_on) as last_prescription,
            count(*)         as n_prescriptions
        from silver.silver_medications
        where medication_flag is not null
        group by patient_id, drug_class
        having count(*) >= 3
    ),
    recent_encounters as (
        select distinct patient_id
        from silver.silver_encounters
        where start_time >= current_timestamp - interval '365 days'
    )
    select count(*) as baseline_count
    from last_fills lf
    inner join recent_encounters re using (patient_id)
    where datediff('day', lf.last_prescription, current_timestamp) >= 180
      and datediff('day', lf.last_prescription, current_timestamp) <= 3650;
""").fetchdf())

print("\n=== Baseline 4 REFINED: no-fill AND had followup encounter ===")
print(con.sql("""
    with discharge_prescriptions as (
        select
            e.patient_id,
            e.encounter_id,
            e.end_time as discharge_date,
            m.drug_class,
            m.authored_on
        from silver.silver_encounters e
        inner join silver.silver_medications m
            on e.encounter_id = m.encounter_id
        where e.is_inpatient = true
          and m.medication_flag is not null
    ),
    followup_fills as (
        select
            dp.patient_id,
            dp.discharge_date,
            dp.drug_class,
            count(m2.medication_request_id) as followup_count
        from discharge_prescriptions dp
        left join silver.silver_medications m2
            on dp.patient_id = m2.patient_id
           and m2.drug_class = dp.drug_class
           and m2.authored_on > dp.discharge_date
           and m2.authored_on <= dp.discharge_date + interval '90 days'
        group by dp.patient_id, dp.discharge_date, dp.drug_class
    ),
    had_followup_encounter as (
        select distinct
            ff.patient_id,
            ff.discharge_date,
            ff.drug_class
        from followup_fills ff
        inner join silver.silver_encounters e2
            on ff.patient_id = e2.patient_id
           and e2.start_time > ff.discharge_date
           and e2.start_time <= ff.discharge_date + interval '90 days'
        where ff.followup_count = 0
    )
    select count(*) as baseline_count
    from had_followup_encounter;
""").fetchdf())