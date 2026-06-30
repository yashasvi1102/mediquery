# MediQuery

Clinical analytics platform with safety-first AI. Synthetic FHIR data flows through a DuckDB Medallion lakehouse, gets modeled as a Neo4j knowledge graph, and is queried through a GraphRAG agent with mandatory citation guards.

**Stack:** Synthea, Python, DuckDB, dbt, Neo4j, LangChain, Ollama, Streamlit

## The clinical data problem this project addresses

Most healthcare data tutorials treat the FHIR `Condition` resource as a list of diseases. It's not. Running a top-10 conditions query on 11,446 synthetic patients revealed that **7 of the 10 most common "conditions" are not clinical disorders** — they're social factors (stress, social isolation), administrative events (medication review due), or employment status.

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

A naive "patients with conditions" query inflates cohorts by counting employed people as sick. The Silver layer separates them using SNOMED hierarchy classification: **only 32.7% of 414,851 conditions are actual disorders.** The remaining 67% are findings (45.4%) and situations (22%). Cohort queries hit `is_billable_diagnosis` or `condition_flag`, never raw SNOMED codes.

![Top conditions query](docs/week1_query_results.png)

## Status

**Days 1–13 of 42 complete.** Bronze + Silver layers built. 33 dbt tests and 14 Python distribution assertions passing.

## What's built so far

- Synthea generating 11,446 synthetic Massachusetts patient bundles
- FHIR parser handling 5 resource types (Patient, Encounter, Condition, MedicationRequest, Observation), including the `medicationReference` fallback that affects 35% of medication rows
- Bronze loader using DuckDB's native `read_parquet()` — 1.67M rows in ~5 seconds
- Silver layer with 5 dbt models:
  - `silver_conditions` with SNOMED hierarchy classification (disorder / finding / situation / unknown)
  - `silver_medications` with therapeutic cohort flags and 15-class drug classification
  - `silver_observations` with `is_plausible_value` and `is_critical_value` flags
  - All foreign-key relationships against `silver_patients` verified (zero orphans)
- 33 dbt schema tests passing (unique, not_null, accepted_values, relationships)
- Python validation suite enforcing every quantitative claim in `docs/design_decisions.md`

## Data quality

Two layers of validation on the silver tier.

**dbt tests** cover schema invariants: primary key uniqueness, not-null constraints, enum values, foreign keys. Run via `dbt test --select silver`.

**Python validation suite** (`tests/validate_silver.py`) covers distribution claims that dbt cannot:

- DD-001 SNOMED classification: disorder share 0.30–0.36 (actual 0.327), finding share 0.42–0.48 (actual 0.454)
- DD-002 Synthea HbA1c plausibility: 49% of HbA1c readings are clinically impossible (<4.0%, incompatible with life). Documented and enforced so downstream analytics opt in via `is_plausible_value`
- Cohort sanity: T2DM cohort 1,700–1,760 patients (actual 1,731); T2DM diabetes-drug treatment rate 0.62–0.72 (actual 0.675)

Run via `python -m tests.validate_silver`. Distribution drift fails by name, not silently.

![dbt lineage: bronze sources to silver models](docs/lineage_silver.png)

## Silver layer row counts

| Table | Rows | Notes |
|---|---|---|
| silver_patients | 11,446 | Deduplicated by load_timestamp |
| silver_encounters | 669,189 | FHIR class codes mapped to readable types |
| silver_conditions | 414,851 | 32.7% disorders, 45.4% findings, 21.7% situations |
| silver_medications | 574,828 | medicationReference fallback fix recovered 202,708 rows |
| silver_observations | 8,348,416 | ~49% of HbA1c readings flagged implausible |

## Stack rationale

- **DuckDB** instead of Snowflake — Snowflake's 30-day trial expires mid-project. DuckDB gives the same SQL surface, same dbt workflow, indefinite demo lifetime. SQL stays portable.
- **Ollama** instead of OpenAI API — local LLM, no API costs, no rate limits.
- **dbt** for Silver/Gold transformations — industry-standard analytics engineering tool.
- **Neo4j Aura** free tier for the clinical knowledge graph.

## Roadmap

- ✅ Week 1: FHIR ingestion + Bronze layer
- ✅ Week 2: dbt Silver transformations + tests + validation suite
- 🚧 Week 3: Gold clinical metrics (readmissions, chronic conditions, adherence)
- ⬜ Week 4: Neo4j knowledge graph
- ⬜ Week 5: GraphRAG agent with citation guards
- ⬜ Week 6: Multi-persona dashboard + anomaly detection benchmarks

## Design decisions

See `docs/design_decisions.md` for the full rationale behind non-obvious choices, including:
- DD-001: SNOMED hierarchy classification in silver_conditions
- DD-002: Synthea's lack of clinical realism in observation values, and the pivot from outcomes-based to prescription-pattern medication adherence