import duckdb
con = duckdb.connect('mediquery.duckdb')
print("\n=== Clinical AND unplanned 30-day rate (real signal) ===")
print(con.sql("""
    select
        count(*)                                                as pairs,
        sum(case when is_30_day_readmission then 1 else 0 end)  as thirty_day,
        round(100.0 * sum(case when is_30_day_readmission then 1 else 0 end) / count(*), 2) as pct_30d
    from gold.gold_readmissions
    where is_likely_planned = false
      and readmission_reason_is_clinical = true
""").fetchdf())

print("\n=== Top 10 CLINICAL AND UNPLANNED 30-day readmissions ===")
print(con.sql("""
    select
        readmission_reason_display,
        count(*) as n
    from gold.gold_readmissions
    where is_30_day_readmission = true
      and is_likely_planned = false
      and readmission_reason_is_clinical = true
    group by readmission_reason_display
    order by n desc
    limit 10
""").fetchdf())

print("=== What is_likely_planned removed ===")
print(con.sql("""
    select
        readmission_reason_display,
        count(*) as n
    from gold.gold_readmissions
    where is_likely_planned = true
    group by readmission_reason_display
    order by n desc
    limit 15
""").fetchdf())

print("\n=== Top 10 UNPLANNED 30-day readmissions ===")
print(con.sql("""
    select
        readmission_reason_display,
        readmission_reason_category,
        count(*) as n
    from gold.gold_readmissions
    where is_30_day_readmission = true
      and is_likely_planned = false
    group by readmission_reason_display, readmission_reason_category
    order by n desc
    limit 10
""").fetchdf())

print("\n=== Total pair split by planned/unplanned ===")
print(con.sql("""
    select
        is_likely_planned,
        count(*) as pairs,
        sum(case when is_30_day_readmission then 1 else 0 end) as thirty_day,
        round(100.0 * sum(case when is_30_day_readmission then 1 else 0 end) / count(*), 2) as pct_30d
    from gold.gold_readmissions
    group by is_likely_planned
""").fetchdf())