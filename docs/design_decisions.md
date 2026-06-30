# Design Decisions

## DD-001: SNOMED hierarchy classification in Silver Conditions (Day 10)

**Problem found Day 7:** Top 10 conditions across 11,446 patients are 70% non-clinical:

| Rank | Condition | Count | Type |
|---|---|---|---|
| 1 | Medication review due (situation) | 82,171 | Administrative |
| 2 | Stress (finding) | 32,447 | SDOH |
| 3 | Gingivitis (disorder) | 30,703 | Clinical |
| 4 | Full-time employment (finding) | 29,885 | SDOH |
| 5 | Part-time employment (finding) | 18,574 | SDOH |
| 6 | Social isolation (finding) | 11,689 | SDOH |
| 7 | Viral sinusitis (disorder) | 11,631 | Clinical |
| 8 | Limited social contact (finding) | 11,561 | SDOH |
| 9 | Not in labor force (finding) | 10,431 | SDOH |
| 10 | Gingival disease (disorder) | 8,951 | Clinical |

**Decision:** Silver Conditions table will include three classification columns:
- `clinical_category` — disorder / finding / situation (from SNOMED suffix)
- `clinical_subcategory` — disease system if disorder, SDOH domain if finding
- `is_billable_diagnosis` — boolean

**Why this matters:** Any naive "find patients with conditions" query inflates cohorts. Hospital analytics and AI cohort builders need this filter or they produce wrong numbers.

**Where this shows up:**
- Day 10: Silver Conditions classification logic
- Day 12: dbt test that fails if SDOH leaks into disease-filter queries
- Day 26: Clinical query library uses `is_billable_diagnosis = true` by default
- Day 32: AI agent confidence drops if it queries without the filter
- Day 41: Blog post writes itself from this finding

## DD-002: Synthea Doesn't Link Diagnoses to Observation Values

**Date:** 2026-06-30 (Day 12)
**Status:** Accepted limitation; pivot Day 17 plan

### Context

While building silver_observations, ran sanity checks on HbA1c values for
T2DM patients and on blood pressure for hypertensive patients. Expected
clinically realistic distributions for each.

### Findings

**HbA1c:**
- 49% of all HbA1c readings (44,108 of 90,453) are below 4.0% — biologically
  impossible (incompatible with life). Population average before filtering:
  3.72%. After filtering implausible values: 5.9%.
- T2DM patients' average HbA1c after filtering: 5.6%. Lower than the
  population average. Real-world T2DM HbA1c averages 7-8% (uncontrolled to
  mildly controlled).

**Blood pressure:**
- Hypertensive patients: average systolic 116.7 mmHg across 68,012 readings.
- Non-hypertensive control group: average systolic 116.6 mmHg across 97,425
  readings.
- 0.1 mmHg difference. Real-world gap would be 15-25 mmHg.

### Root cause

Synthea assigns ICD/SNOMED diagnosis codes to patients but does not generate
correlated observation values. Diagnosed hypertensives are not given
elevated BP. Diagnosed diabetics are not given elevated HbA1c. This is a
known limitation of Synthea's underlying clinical generator — it models
encounter and diagnosis workflows but not pathophysiology.

### Decision

1. Keep all observation values in silver_observations. Do not silently
   filter. Add is_plausible_value boolean flag for downstream models to
   opt into clinically valid ranges.
2. Pivot Day 17 medication adherence narrative away from clinical-outcome
   metrics (HbA1c drop, BP drop) toward prescription-pattern metrics:
   - Treatment rate per condition (fill ratio)
   - Persistence (days between first and last prescription)
   - Coverage gaps (intervals between consecutive prescriptions of same drug)
   This aligns with industry-standard Proportion of Days Covered (PDC)
   methodology used in real medication adherence research where lab data
   is sparse or unreliable.
3. Document the limitation transparently in README and interview talking
   points. "I found and surfaced a synthetic data quality issue rather than
   hiding it" is a stronger story than fake clinical outcomes.

### Consequences

- Day 17 medication adherence becomes prescription-fill-pattern analysis,
  not lab-outcome analysis.
- Day 19 anomaly framework loses one possible anomaly type ("HbA1c spike
  in diabetic without medication change") but retains the other three
  (drug-drug interactions, early readmission, missed annual screening).
- Project's safety-first positioning is strengthened: the is_plausible_value
  flag and the transparent README disclosure demonstrate the data-quality
  rigor the project is supposed to model.