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