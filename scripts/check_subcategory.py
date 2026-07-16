import duckdb
con = duckdb.connect('mediquery.duckdb')

print("=== Columns in silver.silver_conditions ===")
print(con.sql("""
    select column_name
    from information_schema.columns
    where table_schema = 'silver' and table_name = 'silver_conditions'
""").fetchdf())

print("\n=== clinical_subcategory value distribution ===")
try:
    print(con.sql("""
        select clinical_subcategory, count(*) as n
        from silver.silver_conditions
        where clinical_subcategory is not null
        group by clinical_subcategory
        order by n desc
        limit 20
    """).fetchdf())
except Exception as e:
    print(f"Column doesn't exist or query failed: {e}")