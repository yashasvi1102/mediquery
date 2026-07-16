import duckdb
con = duckdb.connect('mediquery.duckdb', read_only=True)

print("=== drug_class values in silver_medications ===")
print(con.sql("""
    select distinct drug_class, count(*) as n
    from silver.silver_medications
    group by drug_class
    order by n desc
""").fetchdf())

print("\n=== warfarin lookup ===")
print(con.sql("""
    select medication_display, count(*) as n
    from silver.silver_medications
    where medication_display ilike '%warfarin%'
    group by medication_display
""").fetchdf())
print(con.sql("""
    select distinct drug_class
    from silver.silver_medications
    where medication_display ilike '%warfarin%'
""").fetchdf())