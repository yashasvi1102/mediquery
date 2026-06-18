"""
init_database.py

Day 5: Initialize the MediQuery DuckDB database.

Creates bronze/silver/gold schemas and the empty Bronze tables.
Idempotent: re-running does NOT drop or wipe existing data.

Usage:
    python data_engineering/init_database.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from connection import get_connection, get_db_path  # noqa: E402

SCHEMA_FILE = Path(__file__).parent / "duckdb" / "bronze_schema.sql"


def main() -> int:
    db_path = get_db_path()
    print(f"DB path:        {db_path}")
    print(f"Already exists: {db_path.exists()}")
    print(f"Schema file:    {SCHEMA_FILE}\n")

    if not SCHEMA_FILE.exists():
        print(f"ERROR: schema file not found at {SCHEMA_FILE}")
        return 1

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    con = get_connection()
    try:
        con.execute(sql)

        schemas = con.execute("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name IN ('bronze', 'silver', 'gold')
            ORDER BY schema_name
        """).fetchall()
        print(f"Schemas ({len(schemas)}):")
        for (name,) in schemas:
            print(f"  - {name}")

        tables = con.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('bronze', 'silver', 'gold')
            ORDER BY table_schema, table_name
        """).fetchall()

        print(f"\nTables ({len(tables)}):")
        for schema, table in tables:
            row_count = con.execute(
                f"SELECT COUNT(*) FROM {schema}.{table}"
            ).fetchone()[0]
            col_count = con.execute(f"""
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = '{table}'
            """).fetchone()[0]
            print(f"  {schema}.{table:32s}  "
                  f"{col_count:>3} cols  {row_count:>10,} rows")

        print("\nDone.")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())