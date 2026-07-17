# MediQuery

**MediQuery** is a 6-week clinical analytics build. Synthetic FHIR patient data
flows through a DuckDB Medallion lakehouse (Weeks 1-3, complete), a Neo4j
knowledge graph (Week 4), and a GraphRAG agent with mandatory citation guards
(Week 5). Currently at end of Week 3 — Bronze + Silver + 5 Gold models built,
with an anomaly injection benchmark and a validation suite locking every
quantitative claim in the project's design docs.

The project's opinionated stance: **synthetic healthcare data has documented
limitations that portfolio tutorials skip.** This project systematically
audits Synthea against real-world clinical benchmarks and documents five
limitations, each of which changes how a downstream model has to be designed.

**Stack:** Synthea · Python · DuckDB · dbt · Neo4j · LangChain · Ollama · Streamlit

## Status

**Days 1-20 of 42 complete.**

- Bronze + Silver + 5 Gold dbt models built.
- 37 Silver dbt tests + 70 Gold dbt tests + 34 Python distribution assertions passing.
- 5 documented design decisions (DD-001 through DD-005) covering Synthea
  data-quality limitations and how each model works around them.
- Anomaly injection framework operational: 30 warfarin coprescriptions
  + 25 HF 7-day readmissions injected. Post-injection detection = 41 and 30,
  matching pre-injection baseline + injected counts exactly.

## What's built

| Layer | Details | Rows | Tests |
|---|---|---|---|
| Synthea | 11,446 Massachusetts synthetic patients | — | — |
| FHIR Parser | 5 resource types; medicationReference fallback recovers 202K rows | 1.67M | Smoke tests in repo |
| Bronze | DuckDB `read_parquet()` load, ~5s | 1.67M | Row-count assertions |
| Silver | 5 dbt models; SNOMED classifier; therapeutic cohort flags; plausibility flags | 10.0M+ | 33 dbt + 14 Python |
| Gold | 5 dbt models; readmissions, chronic conditions, PDC adherence, per-patient utilization, per-provider volume | ~30K | 71 dbt + 21 Python |
| Anomaly Benchmark | 2 injected anomaly types; ground_truth_anomalies table | 55 injected | 6 Python assertions |

Cross-layer reconciliation is exact: 11,446 patients / 669,189 encounters / 1,089
providers all trace Silver → Gold with zero drift, enforced by Python assertions
in `tests/validate_gold.py`.

## The clinical data problem this project addresses

Most healthcare data tutorials treat the FHIR `Condition` resource as a list of
diseases. It's not. Running a top-10 conditions query on 11,446 synthetic
patients revealed that **7 of the 10 most common "conditions" are not clinical
disorders** — they're social factors, administrative events, or employment status.

| Rank | Condition | Count | Type |
|---|---|---|---|
| 1 | Medication review due (situation) | 82,171 | Administrative |
| 2 | Stress (finding) | 32,447 | Social factor |
| 3 | Gingivitis (disorder) | 30,703 | Clinical |
| 4 | Full-time employment (finding) | 29,885 | Social factor |
| 5 | Part-time employment (finding) | 18,574 | Social factor |
| 6 | Social isolation (finding) | 11,689 | Social factor |
| 7 | Viral sinusitis (disorder) | 11,631 | Clinical |
| 8 | Limited social contact (finding) | 11,561 | Social factor |
| 9 | Not in labor force (finding) | 10,431 | Social factor |
| 10 | Gingival disease (disorder) | 8,951 | Clinical |

A naive "patients with conditions" query inflates cohorts by counting employed
people as sick. The Silver layer separates them using SNOMED hierarchy
classification: **only 32.7% of 414,851 conditions are actual disorders.**
Cohort queries in Gold hit `is_billable_diagnosis` or `condition_flag` by
convention, never raw SNOMED codes.

Full write-up: `docs/design_decisions.md` DD-001.

## Five documented Synthea limitations

Each finding surfaced during model construction and forced a design change.

| DD | Finding | Model impact |
|---|---|---|
| DD-001 | 67% of FHIR Conditions are SDOH or admin events, not diseases | Silver conditions classifier; Gold filters via `is_billable_diagnosis` |
| DD-002 | 49% of HbA1c readings are clinically impossible (< 4.0%); diagnosed hypertensives show no BP separation from controls | silver_observations exposes `is_plausible_value`; medication adherence pivoted from clinical-outcome to prescription-pattern |
| DD-003 | SNOMED-noise pattern crosses resource boundaries — Encounter.reasonCode also inflated by history/procedure codes | gold_readmissions applies same classifier to `reason_display`; drops 45% → 19% 30-day rate through overlap + planned + clinical filters |
| DD-004 | Synthea emits MedicationRequest but not MedicationDispense; PDC bimodal (64% < 0.25, 20% ≥ 0.80) | gold_medication_adherence ships PDC as informational; persistence_days is the operative adherence signal |
| DD-005 | 2 of 4 anomaly types dropped after baseline analysis (baselines 60x and 19x injection targets) | Anomaly benchmark ships 2 clean measurements (warfarin coprescription, HF early readmission) instead of 4 noisy ones |

Full write-ups: `docs/design_decisions.md`.

## Readmission methodology: 45% → 19% via three filters

`gold_readmissions` computes CMS-aligned 30-day readmission pairs from
`silver_encounters`. Raw Synthea produces a 45.23% 30-day rate — 3x higher than
real-world CMS all-cause (~15%). Three orthogonal filters bring it into range:

| Filter | 30-day rate | Why |
|---|---|---|
| Raw pairs (no filter) | 45.23% | Includes overlapping encounters and oncology follow-ups |
| Overlap exclusion (days_between >= 0) | — | Synthea generates concurrent long-stay + acute encounters that aren't real readmissions |
| + Planned admissions excluded (`is_likely_planned = false`) | — | 63% of raw 30-day hits were lung-cancer TNM staging admissions |
| + Clinical-reason filter (`readmission_reason_is_clinical = true`) | **19.34%** | Removes history codes ("History of CABG") and procedure codes ("Patient transfer to SNF") that Synthea uses as encounter reasons |

Real-world CMS Hospital-Wide Readmission is ~15%. Ours lands at 19.34%.
Top clinical drivers after filtering: heart failure, COVID-19, MI, aortic
valve disease. Real acute readmission patterns.

## Data-quality validation

Two layers.

**dbt tests** cover schema invariants: primary key uniqueness, not-null, enum
values, foreign keys. 33 Silver + 71 Gold tests. Run via `dbt test`.

**Python distribution suite** (`tests/validate_silver.py` and
`tests/validate_gold.py`) covers claims dbt cannot enforce — distribution
shape, cross-layer reconciliation, DD-specific quantitative constraints.
35 assertions total. Run via `python -m tests.validate_silver` and
`python -m tests.validate_gold`.

Every quantitative claim in this README and in `docs/design_decisions.md`
is reproducible from these two commands. Distribution drift fails by name,
not silently.

## Silver + Gold row counts

| Table | Rows |
|---|---|
| silver_patients | 11,446 |
| silver_encounters | 669,189 |
| silver_conditions | 414,851 |
| silver_medications | 574,828 |
| silver_observations | 8,348,416 |
| gold_readmissions | 4,860 |
| gold_chronic_conditions | 4,881 |
| gold_medication_adherence | 8,546 |
| gold_utilization | 11,446 |
| gold_provider_volume | 1,089 |
| gold.ground_truth_anomalies | 55 |

## Stack rationale

- **DuckDB instead of Snowflake.** Same SQL, same dbt workflow, portable to
  Snowflake in a day. Chose local execution so the demo stays reproducible
  after any trial window closes. Trade-off is losing multi-user concurrency
  and cloud-native features — neither needed at this scale.
- **Ollama instead of OpenAI API.** Local LLM, no API costs, no rate limits.
- **dbt** for Silver/Gold transformations — industry-standard analytics
  engineering tool.
- **Neo4j Aura** free tier for the Week 4 clinical knowledge graph.

## Quickstart

Requires Python 3.13, Java 17 (for Synthea), ~2 GB free disk.

```bash
git clone https://github.com/yashasvi1102/mediquery.git
cd mediquery
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Generate synthetic FHIR data (or skip if included sample used)
bash data_generation/run_synthea.sh 1000

# Load Bronze
python data_generation/load_to_bronze.py

# Build Silver + Gold
cd data_engineering/dbt
dbt run
dbt test

# Validate distributions (Silver + Gold)
cd ../..
python -m tests.validate_silver
python -m tests.validate_gold

# Inject anomalies (optional — for Week 6 benchmark work)
python -m data_generation.anomaly_injector
```

## Roadmap

- ✅ Week 1: FHIR ingestion + Bronze layer
- ✅ Week 2: Silver layer + dbt tests + Python distribution suite
- ✅ Week 3: 5 Gold models + anomaly injection framework + DD-003/004/005
- ⬜ Week 4: Neo4j clinical knowledge graph
- ⬜ Week 5: GraphRAG agent with citation guards
- ⬜ Week 6: Multi-persona dashboard + anomaly detection benchmark

## Design decisions

See `docs/design_decisions.md` for full write-ups. Each DD includes context,
findings, decision, consequences.

- DD-001: SNOMED hierarchy classification in Silver Conditions
- DD-002: Synthea's lack of clinical realism in observation values
- DD-003: SNOMED classification pattern applies across FHIR resources, not just Conditions
- DD-004: Synthea prescriptions are authorization events, not dispensing records
- DD-005: 2 of 4 anomaly types dropped after baseline analysis

## Cleanup backlog (Week 4+)

- Add `dbt-utils` package for `unique_combination_of_columns` tests currently
  enforced in Python
- Extend silver_medications drug_class taxonomy with `anticoagulant` and
  `antiplatelet` classes (warfarin currently sits under `other`)
- Extend silver_conditions with `clinical_subcategory` (disease-system-level
  classification for gold_provider_volume top-category signal)
- Replace `datetime.utcnow()` with `datetime.now(datetime.UTC)`