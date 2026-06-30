import duckdb
from pathlib import Path

con = duckdb.connect(r"D:\Projects\mediquery\mediquery.duckdb")
sql = Path(r"data_engineering\schema\bronze_schema.sql").read_text(encoding="utf-8")
con.execute(sql)
print("Schema applied.")
print()
print(con.execute("select table_schema, table_name from information_schema.tables where table_schema='bronze' order by 1,2").fetchdf().to_string())
