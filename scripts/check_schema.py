import duckdb
con = duckdb.connect('mediquery.duckdb')
print(con.sql("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_name LIKE 'silver_%'
    ORDER BY table_schema, table_name
""").fetchdf())