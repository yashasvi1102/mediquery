import duckdb
con = duckdb.connect('mediquery.duckdb')
print(con.sql("""
    select
        count(*)                                              as total,
        count(provider_id)                                    as with_provider,
        count(distinct provider_id)                           as unique_providers,
        round(100.0 * count(provider_id) / count(*), 1)       as pct_populated
    from silver.silver_encounters
""").fetchdf())
