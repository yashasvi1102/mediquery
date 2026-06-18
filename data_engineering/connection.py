"""
connection.py

DuckDB connection helper for MediQuery.

The database lives at the project root as mediquery.duckdb.
All loaders, dbt configs, and analysis scripts should use get_connection()
rather than constructing paths themselves — keeps the DB location swappable.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "mediquery.duckdb"


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """
    Open a connection to the MediQuery DuckDB database.

    Args:
        read_only: True for analysis scripts that should not mutate state.
                   Multiple read-only connections can coexist; only one
                   writer at a time.
    """
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def get_db_path() -> Path:
    return DB_PATH


if __name__ == "__main__":
    # Quick connection check
    print(f"DB path: {DB_PATH}")
    print(f"DB exists: {DB_PATH.exists()}")
    if DB_PATH.exists():
        size_mb = DB_PATH.stat().st_size / 1_000_000
        print(f"DB size:  {size_mb:.2f} MB")
        con = get_connection(read_only=True)
        try:
            version = con.execute("SELECT version()").fetchone()[0]
            print(f"DuckDB version: {version}")
        finally:
            con.close()