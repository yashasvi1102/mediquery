"""
parse_all_bundles.py

Day 4 deliverable: scale fhir_parser.py from 1 bundle to thousands.

Walks synthea/output/fhir/, parses each patient bundle, skips the
hospitalInformation/practitionerInformation files (not patient bundles),
logs and continues past any bad bundles, and writes 4 parquet files
(one per resource type) into data_generation/parsed/.

Day 6 will pick up those parquet files and load them into DuckDB Bronze.

Usage:
    python data_generation/parse_all_bundles.py                  # parse all
    python data_generation/parse_all_bundles.py --limit 10       # test on 10
    python data_generation/parse_all_bundles.py --limit 100      # test on 100
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

# Make fhir_parser importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))
from fhir_parser import load_bundle, parse_bundle  # noqa: E402


# --- default paths ---
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_FHIR_DIR = PROJECT_ROOT / "synthea" / "output" / "fhir"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "parsed"
ERROR_LOG = SCRIPT_DIR / "parse_errors.log"


def find_patient_bundles(fhir_dir: Path) -> list[Path]:
    """All patient bundle paths; skips hospitalInformation/practitionerInformation."""
    return sorted(
        f for f in fhir_dir.glob("*.json")
        if not f.name.startswith("hospitalInformation")
        and not f.name.startswith("practitionerInformation")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse all Synthea bundles to parquet.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only parse the first N bundles (for testing).")
    ap.add_argument("--fhir-dir", type=Path, default=DEFAULT_FHIR_DIR,
                    help="Synthea FHIR output directory.")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                    help="Where to write parquet files.")
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=ERROR_LOG,
        filemode="w",
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    bundles = find_patient_bundles(args.fhir_dir)
    if args.limit:
        bundles = bundles[: args.limit]

    if not bundles:
        print(f"No patient bundles found in {args.fhir_dir}")
        print("Did Synthea actually run? Check synthea/output/fhir/")
        return 1

    print(f"Parsing {len(bundles):,} bundles from {args.fhir_dir}")
    print(f"Errors (if any) -> {ERROR_LOG}\n")

    accumulators: dict[str, list[dict]] = {
        "patients": [],
        "encounters": [],
        "conditions": [],
        "medication_requests": [],
    }

    successful = 0
    failed = 0
    start = time.time()

    # progress cadence: more often for small batches, less for full runs
    report_every = 10 if len(bundles) <= 100 else 500

    for i, bundle_path in enumerate(bundles, start=1):
        try:
            bundle = load_bundle(bundle_path)
            parsed = parse_bundle(bundle)
            for key in accumulators:
                accumulators[key].extend(parsed[key])
            successful += 1
        except Exception as exc:
            logging.warning("Failed: %s | %s: %s", bundle_path.name,
                            type(exc).__name__, exc)
            failed += 1

        if i % report_every == 0 or i == len(bundles):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(bundles) - i) / rate if rate > 0 else 0
            print(f"  [{i:>6,}/{len(bundles):,}]  "
                  f"{rate:6.1f} bundles/sec  "
                  f"ok={successful:,} fail={failed:,}  "
                  f"eta={eta:.0f}s")

    elapsed = time.time() - start
    print(f"\nParsed {successful:,} bundles in {elapsed:.1f}s "
          f"({successful / elapsed:.1f} bundles/sec)")
    if failed:
        print(f"WARNING: {failed} bundles failed -> see {ERROR_LOG}")

    print(f"\nWriting parquet to {args.output_dir}/")
    for resource_key, records in accumulators.items():
        df = pd.DataFrame(records)
        out_path = args.output_dir / f"{resource_key}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"  {resource_key:25s} {len(df):>10,} rows  ->  {out_path.name}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())