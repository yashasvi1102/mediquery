import duckdb
con = duckdb.connect('mediquery.duckdb')

print("=== Row counts by medication_flag ===")
print(con.sql("""
    select
        medication_flag,
        count(*)                                   as patient_class_pairs,
        count(distinct patient_id)                 as unique_patients
    from gold.gold_medication_adherence
    group by medication_flag
    order by patient_class_pairs desc
""").fetchdf())

print("\n=== Adherence class distribution overall ===")
print(con.sql("""
    select
        adherence_class,
        count(*) as n,
        round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
    from gold.gold_medication_adherence
    group by adherence_class
    order by n desc
""").fetchdf())

print("\n=== Adherence class by medication_flag ===")
print(con.sql("""
    select
        medication_flag,
        adherence_class,
        count(*) as n
    from gold.gold_medication_adherence
    group by medication_flag, adherence_class
    order by medication_flag, adherence_class
""").fetchdf())

print("\n=== PDC distribution ===")
print(con.sql("""
    select
        medication_flag,
        round(avg(pdc), 3)                              as avg_pdc,
        round(percentile_cont(0.5) within group (order by pdc), 3) as median_pdc,
        round(min(pdc), 3)                              as min_pdc,
        round(max(pdc), 3)                              as max_pdc,
        count(case when pdc >= 0.80 then 1 end)         as adherent_count,
        count(*)                                        as total,
        round(100.0 * count(case when pdc >= 0.80 then 1 end) / count(*), 1) as pct_adherent
    from gold.gold_medication_adherence
    where pdc is not null
    group by medication_flag
    order by medication_flag
""").fetchdf())

print("\n=== Prescription count distribution ===")
print(con.sql("""
    select
        case
            when prescription_count = 1 then '1'
            when prescription_count between 2 and 5 then '2-5'
            when prescription_count between 6 and 20 then '6-20'
            when prescription_count between 21 and 50 then '21-50'
            else '50+'
        end as bucket,
        count(*) as n
    from gold.gold_medication_adherence
    group by bucket
    order by
        case bucket
            when '1' then 1 when '2-5' then 2 when '6-20' then 3
            when '21-50' then 4 else 5 end
""").fetchdf())

print("\n=== Measurement period distribution ===")
print(con.sql("""
    select
        round(avg(measurement_period_days), 0) as avg_days,
        round(min(measurement_period_days), 0) as min_days,
        round(max(measurement_period_days), 0) as max_days,
        count(case when measurement_period_days = 0 then 1 end) as single_day_windows
    from gold.gold_medication_adherence
""").fetchdf())
print("\n=== PDC vs lifetime_coverage_ratio (are they telling different stories?) ===")
print(con.sql("""
    select
        medication_flag,
        round(avg(pdc), 3)                     as avg_pdc_365,
        round(avg(lifetime_coverage_ratio), 3) as avg_lifetime,
        round(avg(pdc) - avg(lifetime_coverage_ratio), 3) as diff
    from gold.gold_medication_adherence
    where pdc is not null and lifetime_coverage_ratio is not null
    group by medication_flag
    order by medication_flag
""").fetchdf())

print("\n=== Fills in 365-day window ===")
print(con.sql("""
    select
        medication_flag,
        round(avg(fills_in_window), 1)       as avg_fills,
        min(fills_in_window)                 as min_fills,
        max(fills_in_window)                 as max_fills
    from gold.gold_medication_adherence
    group by medication_flag
    order by medication_flag
""").fetchdf())
print("\n=== Persistence (days from first to last prescription) ===")
print(con.sql("""
    select
        medication_flag,
        round(avg(persistence_days), 0)                                    as avg_persistence,
        round(percentile_cont(0.5) within group (order by persistence_days), 0) as median_persistence,
        count(case when persistence_days >= 365 then 1 end)                as gte_1yr,
        count(case when persistence_days >= 1825 then 1 end)               as gte_5yr,
        count(*)                                                            as total
    from gold.gold_medication_adherence
    group by medication_flag
    order by medication_flag
""").fetchdf())

print("\n=== PDC distribution histogram ===")
print(con.sql("""
    select
        case
            when pdc < 0.10 then '0.00-0.10'
            when pdc < 0.25 then '0.10-0.25'
            when pdc < 0.50 then '0.25-0.50'
            when pdc < 0.80 then '0.50-0.80'
            else '0.80-1.00'
        end as pdc_bucket,
        count(*) as n
    from gold.gold_medication_adherence
    where pdc is not null
    group by pdc_bucket
    order by pdc_bucket
""").fetchdf())