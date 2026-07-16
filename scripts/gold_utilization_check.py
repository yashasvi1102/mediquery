import duckdb
con = duckdb.connect('mediquery.duckdb')

print("=== gold_utilization row count vs silver_patients ===")
print(con.sql("""
    select
        (select count(*) from silver.silver_patients)                    as silver_patients_count,
        (select count(*) from gold.gold_utilization)                     as gold_utilization_count,
        (select count(*) from gold.gold_utilization where has_no_encounters = true) as zero_encounter_patients
""").fetchdf())

print("\n=== Encounter volume per patient (distribution) ===")
print(con.sql("""
    select
        round(avg(total_encounters), 1)                                 as avg,
        round(percentile_cont(0.5) within group (order by total_encounters), 0) as median,
        min(total_encounters)                                           as min_val,
        max(total_encounters)                                           as max_val
    from gold.gold_utilization
""").fetchdf())

print("\n=== Encounter type mix (totals across all patients) ===")
print(con.sql("""
    select
        sum(ambulatory_encounters)                                      as ambulatory,
        sum(emergency_encounters)                                       as emergency,
        sum(inpatient_encounters)                                       as inpatient,
        sum(home_health_encounters)                                     as home_health,
        sum(virtual_encounters)                                         as virtual,
        sum(total_encounters)                                           as total
    from gold.gold_utilization
""").fetchdf())

print("\n=== gold_provider_volume row count vs silver providers ===")
print(con.sql("""
    select
        (select count(distinct provider_id) from silver.silver_encounters) as silver_providers,
        (select count(*) from gold.gold_provider_volume)                    as gold_providers
""").fetchdf())

print("\n=== Provider volume distribution ===")
print(con.sql("""
    select
        round(avg(total_encounters), 0)                                 as avg_encounters,
        round(percentile_cont(0.5) within group (order by total_encounters), 0) as median_encounters,
        min(total_encounters)                                           as min_encounters,
        max(total_encounters)                                           as max_encounters,
        round(avg(unique_patients), 0)                                  as avg_patients,
        max(unique_patients)                                            as max_patients
    from gold.gold_provider_volume
""").fetchdf())

print("\n=== Top 10 providers by encounter volume ===")
print(con.sql("""
    select
        provider_id,
        total_encounters,
        unique_patients,
        avg_encounters_per_patient,
        top_clinical_category
    from gold.gold_provider_volume
    order by total_encounters desc
    limit 10
""").fetchdf())

print("\n=== Top clinical category distribution ===")
print(con.sql("""
    select
        top_clinical_category,
        count(*) as providers,
        round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
    from gold.gold_provider_volume
    group by top_clinical_category
    order by providers desc
""").fetchdf())