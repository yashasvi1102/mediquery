"""
Cross-layer silver validation for MediQuery.

Goes beyond `dbt test` by encoding the data-quality findings from
DD-001 (SNOMED classification) and DD-002 (Synthea HbA1c plausibility)
as numeric range assertions. Run after every silver rebuild.

These checks would fail if:
  - Synthea regenerated with different parameters
  - A classification rule in silver_conditions changed
  - The is_plausible_value threshold in silver_observations changed
  - Cohort flag logic in silver_conditions changed

That's the point. The script tells you WHICH assumption broke,
not just that something is different.
"""
import sys
from data_engineering.connection import get_connection


CHECKS = []


def check(name, sql, ok_fn, expected_desc, notes=""):
    CHECKS.append((name, sql, ok_fn, expected_desc, notes))


# ---------- Referential integrity ----------
# dbt's relationships tests cover patient_id FKs. These check the
# inverse direction: every parent has at least the volume we expect.

check(
    "silver_patients row count",
    "SELECT COUNT(*) FROM silver.silver_patients",
    lambda v: v == 11446,
    "== 11446",
    "Locked to Synthea-MA Day 4 run. Regenerate => update.",
)

check(
    "silver_encounters row count",
    "SELECT COUNT(*) FROM silver.silver_encounters",
    lambda v: v == 669189,
    "== 669189",
)

check(
    "silver_conditions row count",
    "SELECT COUNT(*) FROM silver.silver_conditions",
    lambda v: v == 414851,
    "== 414851",
)

check(
    "silver_medications row count",
    "SELECT COUNT(*) FROM silver.silver_medications",
    lambda v: v == 574828,
    "== 574828",
)

check(
    "silver_observations row count",
    "SELECT COUNT(*) FROM silver.silver_observations",
    lambda v: v == 8348416,
    "== 8348416",
)


# ---------- DD-001: SNOMED clinical classification ----------

check(
    "DD-001 disorder share is ~33%",
    """
    SELECT 1.0 * COUNT(*) FILTER (WHERE clinical_category = 'disorder')
                 / COUNT(*)
    FROM silver.silver_conditions
    """,
    lambda v: 0.30 <= v <= 0.36,
    "0.30 .. 0.36",
    "Per LEARNINGS Day 10: 135,775 / 414,851 = 32.7%",
)

check(
    "DD-001 finding share is ~45%",
    """
    SELECT 1.0 * COUNT(*) FILTER (WHERE clinical_category = 'finding')
                 / COUNT(*)
    FROM silver.silver_conditions
    """,
    lambda v: 0.42 <= v <= 0.48,
    "0.42 .. 0.48",
)

check(
    "DD-001 unknown category below 1%",
    """
    SELECT 1.0 * COUNT(*) FILTER (WHERE clinical_category = 'unknown')
                 / COUNT(*)
    FROM silver.silver_conditions
    """,
    lambda v: v < 0.01,
    "< 0.01",
    "If this rises, the SNOMED suffix regex needs a new mapping.",
)

check(
    "is_billable_diagnosis matches clinical_category='disorder'",
    """
    SELECT COUNT(*) FROM silver.silver_conditions
    WHERE is_billable_diagnosis <> (clinical_category = 'disorder')
    """,
    lambda v: v == 0,
    "== 0",
    "Day 10 gotcha: these two columns must stay in lockstep.",
)


# ---------- DD-001 cohort sanity ----------

check(
    "T2DM cohort patient count",
    """
    SELECT COUNT(DISTINCT patient_id)
    FROM silver.silver_conditions
    WHERE condition_flag = 'diabetes_t2'
    """,
    lambda v: 1700 <= v <= 1760,
    "1700 .. 1760",
    "LEARNINGS Day 10: 1,731 patients across 4,189 rows.",
)

check(
    "Hypertension cohort patient count",
    """
    SELECT COUNT(DISTINCT patient_id)
    FROM silver.silver_conditions
    WHERE condition_flag = 'hypertension'
    """,
    lambda v: 2600 <= v <= 2730,
    "2600 .. 2730",
)

check(
    "Heart failure cohort patient count",
    """
    SELECT COUNT(DISTINCT patient_id)
    FROM silver.silver_conditions
    WHERE condition_flag = 'heart_failure'
    """,
    lambda v: 300 <= v <= 340,
    "300 .. 340",
)


# ---------- DD-002: Synthea HbA1c plausibility ----------

check(
    "DD-002 HbA1c implausible share is ~49%",
    """
    SELECT 1.0 * COUNT(*) FILTER (WHERE NOT is_plausible_value)
                 / COUNT(*)
    FROM silver.silver_observations
    WHERE observation_kind = 'hba1c'
    """,
    lambda v: 0.45 <= v <= 0.55,
    "0.45 .. 0.55",
    "Headline finding: ~49% of HbA1c readings are clinically impossible (<4.0%).",
)


# ---------- DD-001 + Day 11 cross-check: T2DM treatment rate ----------

check(
    "T2DM diabetes_drug treatment rate is ~67%",
    """
    WITH t2dm AS (
        SELECT DISTINCT patient_id
        FROM silver.silver_conditions
        WHERE condition_flag = 'diabetes_t2'
    ),
    treated AS (
        SELECT DISTINCT m.patient_id
        FROM silver.silver_medications m
        JOIN t2dm USING (patient_id)
        WHERE m.medication_flag = 'diabetes_drug'
    )
    SELECT 1.0 * (SELECT COUNT(*) FROM treated)
                 / (SELECT COUNT(*) FROM t2dm)
    """,
    lambda v: 0.62 <= v <= 0.72,
    "0.62 .. 0.72",
    "Day 11 insulin inclusion decision: rate was 28% without insulin, 67.5% with.",
)


# ---------- Runner ----------

def main():
    conn = get_connection()
    failed = []
    print(f"\nRunning {len(CHECKS)} silver validation checks...\n")
    for name, sql, ok, desc, notes in CHECKS:
        try:
            v = conn.execute(sql).fetchone()[0]
        except Exception as e:
            print(f"[ERROR] {name}: query failed -> {e}")
            failed.append(name)
            continue
        passed = ok(v)
        marker = "PASS" if passed else "FAIL"
        # Format float values readably
        v_str = f"{v:.4f}" if isinstance(v, float) else str(v)
        print(f"[{marker}] {name}")
        print(f"       got={v_str}  expected {desc}")
        if notes:
            print(f"       note: {notes}")
        if not passed:
            failed.append(name)
    print()
    if failed:
        print(f"FAILED: {len(failed)} of {len(CHECKS)} checks")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    print(f"OK: all {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()