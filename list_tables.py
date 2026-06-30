import duckdb
con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")
print(con.execute("select table_schema, table_name from information_schema.tables where table_schema in ('bronze','silver','gold') order by 1,2").fetchdf().to_string())
