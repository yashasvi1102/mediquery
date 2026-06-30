"""
load_bronze.py

Day 6: Load Bronze tables from the parquet intermediate written by Day 4's
parse_all_bundles.py.

Uses DuckDB's native read_parquet() — much faster than row-by-row Python
inserts. Total load for 1.7M rows should complete in under 10 seconds.

Default mode: TRUNCATE then INSERT. Re-running gives a clean, predictable
state during development.

Pass --append to add a new batch without truncating (preserves load history
across runs — closer to production Medallion semantics).

Usage:
    python data_engineering/load_bronze.py
    python data_engineering/load_bronze.py --append
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from connection import get_connection  # noqa: E402

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_PARSED_DIR = PROJECT_ROOT / "data_generation" / "parsed"

# Each parquet file -> (target Bronze table, column list excluding audit cols).
# Column order matches the schema declaration and the parser output.
TABLE_CONFIG: dict[str, tuple[str, list[str]]] = {
    "patients.parquet": (
        "bronze.bronze_patients",
        [
            "patient_id", "given_name", "family_name", "gender",
            "birth_date", "deceased_date", "marital_status", "race",
            "ethnicity", "city", "state", "postal_code", "country",
        ],
    ),
    "encounters.parquet": (
        "bronze.bronze_encounters",
        [
            "encounter_id", "patient_id", "status",
            "class_code", "class_display", "type_code", "type_display",
            "reason_code", "reason_display",
            "start_time", "end_time", "provider_id",
        ],
    ),
    "conditions.parquet": (
        "bronze.bronze_conditions",
        [
            "condition_id", "patient_id", "encounter_id",
            "code_system", "code", "display",
            "clinical_status", "verification_status",
            "onset_date", "abatement_date", "recorded_date",
        ],
    ),
    "medication_requests.parquet": (
        "bronze.bronze_medication_requests",
        [
            "medication_request_id", "patient_id", "encounter_id",
            "status", "intent",
            "code_system", "medication_code", "medication_display",
            "authored_on", "dosage_text",
        ],
    ),
    "observations.parquet": (
        "bronze.bronze_observations",
        [
            "observation_id", "parent_observation_id",
            "patient_id", "encounter_id",
            "status", "category",
            "loinc_code", "loinc_display",
            "value_numeric", "value_text",
            "value_code", "value_code_system", "unit",
            "effective_date", "issued_date",
        ],
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Load Bronze tables from parquet.")
    ap.add_argument(
        "--append", action="store_true",
        help="Append a new batch without truncating (default truncates).",
    )
    ap.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    args = ap.parse_args()

    batch_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    mode = "APPEND" if args.append else "TRUNCATE+INSERT"

    print(f"Mode:     {mode}")
    print(f"Batch ID: {batch_id}")
    print(f"Source:   {args.parsed_dir}\n")

    missing = [p for p in TABLE_CONFIG if not (args.parsed_dir / p).exists()]
    if missing:
        print(f"ERROR: missing parquet files: {missing}")
        print("Run data_generation/parse_all_bundles.py first.")
        return 1

    con = get_connection()
    total_start = time.time()
    try:
        for parquet_name, (table, columns) in TABLE_CONFIG.items():
            parquet_path = (args.parsed_dir / parquet_name).resolve().as_posix()
            cols_sql = ", ".join(columns)

            t0 = time.time()

            source_rows = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{parquet_path}')"
            ).fetchone()[0]

            if not args.append:
                con.execute(f"TRUNCATE TABLE {table}")

            con.execute(f"""
                INSERT INTO {table} ({cols_sql}, load_batch_id)
                SELECT {cols_sql}, ? AS load_batch_id
                FROM read_parquet('{parquet_path}')
            """, [batch_id])

            loaded_rows = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE load_batch_id = ?",
                [batch_id],
            ).fetchone()[0]

            elapsed = time.time() - t0
            ok = "OK" if source_rows == loaded_rows else "MISMATCH"
            print(f"  {table:42s}  "
                  f"parquet={source_rows:>10,}  "
                  f"loaded={loaded_rows:>10,}  "
                  f"{elapsed:5.2f}s  {ok}")

        print(f"\nTotal time: {time.time() - total_start:.2f}s")

        print("\nFinal Bronze state (all batches):")
        for _, (table, _) in TABLE_CONFIG.items():
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            batches = con.execute(
                f"SELECT COUNT(DISTINCT load_batch_id) FROM {table}"
            ).fetchone()[0]
            print(f"  {table:42s}  {total:>10,} rows  ({batches} batch(es))")

        print("\nDone.")
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())