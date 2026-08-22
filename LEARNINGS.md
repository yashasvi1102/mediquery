# MediQuery Learnings Log

A running log of decisions, mistakes, and surprises during this 6-week build.

## Day 1
- Chose DuckDB over Snowflake to avoid 30-day trial expiry killing the demo later.
- Kept Python 3.13 (every library this project uses supports 3.13 in 2026).
- Installed Java 17 Temurin for Synthea. Java is only needed by Synthea, nothing else.
- GitHub username: yashasvi1102.
- Synthea FHIR bundles are JSON arrays under "entry". Each entry wraps a resource.
- Patients have ONE Patient resource but DOZENS of Encounter/Condition/Observation resources.
- ICD-10 codes are at entry[].resource.code.coding[0].code, but SNOMED codes are also present at coding[1]. Parser must pick the right system.
- File size correlates with patient age - older = more medical history = bigger bundle.
## Day 2
- Set up Python venv in project root, not inside synthea/. Keeping our code separate from the tool that generates data is cleaner.
- One Synthea patient = 500-1200 entries across ~19 resource types. Way more data per patient than expected.
- Bryan958 had more Procedures (176) than Observations (103), which is unusual - most patients have many more vitals/labs than procedures.
- Of 19 resource types Synthea emits, only 6 matter for this project: Patient, Encounter, Condition, MedicationRequest, Observation, Procedure. The other 13 are billing/admin noise.
## Day 3 - FHIR Parser

- Built extractors for Patient, Encounter, Condition, MedicationRequest.
- FHIR references come in 3 formats: urn:uuid:<id>, ResourceType/<id>, 
  and conditional ResourceType?identifier=<system>|<value>. Parser handles all.
- Provider/Organization refs use the conditional format because they live 
  in separate hospitalInformation/practitionerInformation bundles.
- CONFIRMED: Synthea Conditions emit SNOMED codes ONLY, not ICD-10. 
  The original Day 10 plan needs adjustment — either stay SNOMED-native 
  or build a SNOMED-to-ICD-10 lookup. Decide on Day 9.
- Synthea Conditions include non-clinical entries like "Medication review 
  due (situation)" and "Received higher education (finding)". Bronze keeps 
  everything; Silver layer must filter these before Day 16's chronic-condition work.
- Marital status codes: M=Married, S=Single, D=Divorced, W=Widowed, U=Unknown.
- Regenerating Synthea overwrites synthea/output/fhir/. Note bundle 
  filenames before regenerating.
  ## Day 4 - Batch Parsing at Scale

- Scaled fhir_parser.py from 1 bundle to 11,446 with zero failures.
- Synthea-Massachusetts generates dense per-patient histories: 
  ~58 encounters, ~36 conditions, ~50 medication requests per patient.
  Total row counts: encounters 669K, conditions 415K, medications 575K.
- These numbers will inflate Bronze table sizes — Silver layer filters 
  (especially Condition non-disease findings) become important for Day 16+ work.
- Wrote intermediate parquet output (data_generation/parsed/) for Day 6 
  to load into DuckDB. Avoids re-parsing the FHIR JSON twice.
- Parquet files are 50-200MB total — gitignored, not in version control.
- PowerShell ">> .gitignore" can write UTF-16 BOM that git can't parse. 
  Use Set-Content with -Encoding utf8 to be safe.
  ## Day 5 - DuckDB Bronze Schema

- Created mediquery.duckdb at project root with bronze/silver/gold schemas.
- 4 empty Bronze tables: bronze_patients, bronze_encounters, bronze_conditions,
  bronze_medication_requests. Total 54 columns including audit columns.
- Medallion principle applied: Bronze has NO primary key or unique constraints.
  Raw, append-only, history preserved. Dedup happens in Silver.
- Audit columns on every Bronze table: load_timestamp (default CURRENT_TIMESTAMP)
  and load_batch_id (string identifier per load run). Lets us trace which
  pipeline run produced each row.
- bronze_observations deferred to Day 11 (no parser yet, no point in empty table).
- connection.py wraps duckdb.connect() so DB path is centralized — every
  future loader/script imports get_connection() instead of hardcoding paths.
- DuckDB v1.5.3, 0.27 MB empty. Will grow to ~200-500 MB after Day 6 load.
## Day 6 - Bronze Layer Load

- Loaded 1.67M rows into Bronze in 5.29 seconds using DuckDB's read_parquet().
  Row counts: patients 11,446 | encounters 669,189 | conditions 414,851 |
  medications 574,828. All self-verified (source parquet count == inserted count).
- DuckDB native parquet read is ~100x faster than Python row inserts would
  have been. ETL pattern: write parquet intermediate, load via SQL, not loops.
- DB file is 88.76 MB for 1.67M rows. Better compression than parquet because
  DuckDB stores per-column dictionaries.
- Gender split sanity-checked: 5,669 F / 5,777 M, sums to 11,446 — no nulls.
- TRUNCATE+INSERT chosen as default for dev iteration. --append flag available
  for true Medallion semantics (preserves load history via load_batch_id).
- PowerShell parses asterisks and parens in -c "..." Python strings before
  Python sees them. Use here-strings (@"..."@ | python) or a .py file.
  ## Day 7 — Week 1 Close & Differentiator Decision

- Top-10 conditions query revealed 7 of 10 most common "conditions" are not
  clinical disorders — they're social factors (stress, social isolation),
  administrative events (medication review due), or employment status.
  Synthea encodes SDOH and admin findings in the same table as real diseases.
- This is correct FHIR behavior, not a bug. The Condition resource is designed
  to capture anything clinically relevant including social determinants. But
  it means naive "patients with conditions" queries inflate cohorts.
- Decision: Silver Conditions (Day 10) will add three classification columns —
  clinical_category (disorder/finding/situation), clinical_subcategory (disease
  system if disorder, SDOH domain if finding), is_billable_diagnosis (boolean).
- This becomes the project's main differentiator. Most Synthea tutorials skip
  this filtering. Documented in docs/design_decisions.md (DD-001) so the
  decision survives context switches.
- SNOMED 224299000 ("Received higher education") confirmed present at 5,382
  rows — used initially as the LinkedIn post hook before realizing the
  top-10 distribution was a stronger story.
- README rewritten to lead with the SNOMED differentiator and the actual data
  table, not generic project description. The "what's interesting" section
  comes before the stack list.
  ## Day 8 — dbt Installation

- Installed dbt-duckdb 1.10.1 alongside dbt-core 1.11.11. Plugin attached cleanly,
  no version conflicts with existing project dependencies.
- dbt project lives at data_engineering/dbt/ — kept inside the existing data
  engineering folder rather than as a sibling at repo root. Keeps related
  warehouse work in one place.
- profiles.yml committed to the repo. Normally profiles.yml stays out of git
  because it contains credentials, but the DuckDB target has only a local file
  path — no secrets. Anyone cloning can run dbt debug immediately. If a cloud
  target gets added later, credentials move to env vars.
- DBT_PROFILES_DIR set to the project dbt folder, not ~/.dbt/. Project stays
  portable — no machine-specific config in user home directory.
- Materialization strategy: silver and gold both build as physical tables, not
  views. Views re-execute on every query — wasteful for analytics. Tables cost
  storage but stay fast.
- Empty model folders (models/silver/, models/gold/) need .gitkeep placeholder
  files or git won't track them. Almost shipped without these — would have
  broken Day 9 for anyone cloning the repo.
- dbt debug passed on first try after profiles.yml was created. No SQL written
  yet — that's Day 9.
- YAML is whitespace-sensitive. dbt_project.yml and profiles.yml both use exactly
  2-space indentation. Tabs anywhere would break it.
- Forward slashes in DuckDB path inside profiles.yml — YAML treats backslashes
  as escape characters even on Windows.
  ## Day 9 — silver_patients

- silver_patients built from bronze_patients with row_number() dedup on
  load_timestamp. Same count as Bronze (11,446) — no duplicates from
  current load, but dedup logic is in place for future re-ingests.
- Kept FHIR-native gender values (male/female) instead of mapping to M/F.
  Single-letter codes would have lost FHIR alignment for no real benefit.
- Stored birth_date as source of truth + age_years_current and age_at_death
  as derived helpers. Gold layer will compute age-at-encounter against
  birth_date for accuracy; current_age helper is for fast cohort filters.
- age_years_current is technically non-deterministic (changes daily). Acceptable
  trade-off — documented in column comments. Real age math happens in Gold.
- Address fields were already split in Bronze (city/state/postal_code/country).
  Day 9 plan said "parse address" — that work was done correctly in the FHIR
  parser on Day 3. Don't re-solve solved problems.
- GOTCHA: dbt-duckdb prefixes custom schemas with target.schema by default,
  so +schema: silver in dbt_project.yml created main_silver, not silver.
  Fixed with a generate_schema_name macro override that uses the custom
  schema name as-is. Standard dbt pattern when you don't want the prefix.
- Sources declared in models/sources.yml so silver_patients references
  {{ source('bronze', 'bronze_patients') }} instead of hardcoding the table.
  This wires up dbt lineage — `dbt docs generate` will show Bronze → Silver
  dependencies on Day 13.
- Sanity check: 11,446 unique patients | 5,669 F / 5,777 M | 1,446 deceased
  (12.6%) | all 4 age buckets populated. Distribution matches Synthea-MA
  defaults from Day 6.
  ## Day 10 — silver_encounters + silver_conditions (DD-001 implemented)

- silver_encounters: 669,189 rows, all deduped by encounter_id on load_timestamp.
  FHIR class codes mapped to readable types (AMB->ambulatory, EMER->emergency,
  IMP->inpatient, HH->home_health, VR->virtual). class_display is always NULL
  in Synthea — only the code is emitted, so the mapping is mandatory.
- is_inpatient boolean added for Day 15 readmission logic. 12,223 inpatient
  encounters matches Bronze class_code distribution exactly.
- length_of_stay_days computed in Silver, not Gold. Used by readmissions
  AND length-of-stay analytics; better to compute once.

- silver_conditions: DD-001 implemented. 414,851 conditions classified:
    - 135,775 disorder (33%)
    - 188,477 finding (45%)
    - 89,994 situation (22%)
    - 605 unknown (0.15%)
  This is the headline number: 67% of "conditions" in raw FHIR are NOT diseases.
  Naive cohort queries would inflate by 3x without this filter.

- Classification done via SNOMED suffix regex on the display string. Worked
  for 99.2% of rows on first pass. Top unknowns were sprains and burns
  (morphologic abnormality) and refugees (person) — added these as suffixes
  with mappings: morphologic abnormality -> disorder, person -> finding.
  Brought unknown from 3,206 to 605. Remaining unknowns are SNOMED codes
  with no parenthetical at all (joint pain, opioid abuse, gout) — fundamental
  SNOMED inconsistency, can't fix.

- condition_flag column centralizes SNOMED code lists for the 4 chronic
  conditions: diabetes_t2 (1,731 patients, 4,189 rows), hypertension (2,665),
  heart_failure (321), copd (164). Gold models filter by flag, not by
  hardcoded codes. If Day 16 chronic-condition cohort needs to add a
  condition, one place to update.

- Diabetes T2 includes complications (retinopathy, neuropathy, kidney disease,
  proteinuria) under the diabetes_t2 flag. Patient counts: 4,189 rows /
  1,731 patients = 2.4 conditions per diabetic. Without complications,
  Day 16 cohort would have undercounted.

- GOTCHA: is_billable_diagnosis originally referenced display ilike '%(disorder)%'
  directly. After adding morphologic abnormality -> disorder mapping in
  clinical_category, the two columns disagreed by 2,091 rows. Lesson: when
  a derived column has logic that another column depends on, the dependent
  column must reference the same source. Fixed by mirroring the OR condition.
  In a cleaner build this would be a 2-stage CTE with the second referencing
  the first's clinical_category column.

- 67% non-disease finding is the LinkedIn post for Day 14 weekly close.

## Day 11 — silver_medications + parser fix (medicationReference fallback)

- BUG FOUND BEFORE WRITING MODEL: 202,708 of 574,828 medication rows (35%)
  had NULL medication_display in Bronze. Discovered when running schema check
  before writing silver_medications. Would have silently corrupted Day 17
  adherence, Day 19 anomaly framework, and Day 25 Neo4j PRESCRIBED edges.

- ROOT CAUSE: FHIR allows two ways to attach a drug to a MedicationRequest:
  inline (medicationCodeableConcept) or by reference (medicationReference
  pointing to a Medication resource in the same bundle). Day 3 parser only
  handled the inline form. Synthea uses both; the reference form returned NULL.

- FIX: Added build_medication_lookup() to fhir_parser. Two-pass parsing per
  bundle: first scan extracts a {uuid -> coding} dict from contained
  Medication resources; second pass resolves medicationReference fallbacks
  when medicationCodeableConcept is absent.

- DESIGN CHOICE: Quick fix (special-case MedicationRequest in parse_bundle)
  over clean refactor (every extractor accepts a context dict). Rule used:
  don't generalize until the second instance exists. If Day 13 observations
  parser needs the same pattern, refactor then.

- VERIFIED: re-ran parser on all 11,446 bundles. 574,828 rows. 0 NULL displays,
  0 NULL code_systems. Investigation scripts (find_broken.py, inspect_med.py,
  verify_med_resource.py, smoke_test_parser.py) kept in repo — they document
  the diagnostic process.

- silver_medications: dedup by medication_request_id on load_timestamp.
  Two classification columns:
    - medication_flag (Day 17 cohort: diabetes_drug, antihypertensive,
      heart_failure_drug, copd_drug; NULL for unrelated drugs)
    - drug_class (15 therapeutic categories: biguanide, insulin,
      ace_inhibitor, statin, opioid, etc.)
  Same pattern as silver_conditions: narrow flag for downstream cohorts,
  broad class for analytics.

- INSULIN DECISION: first pass excluded insulin from diabetes_drug flag.
  Cross-check showed only 494 of 1,731 T2DM patients (28%) on a diabetes
  drug — far below real-world ~70-80%. Insulin is Synthea's #2 medication
  overall (50K rows) and is prescribed alongside or instead of metformin
  for T2DM. Added insulin to diabetes_drug: rate jumped to 67.5% (1,168/1,731).
  The remaining 32.5% are likely diabetic complications coded without parent
  T2DM diagnosis — Day 17 caveat.

- HTN treatment rate is 100% in Synthea (every hypertensive gets a drug).
  Real world is ~75%. Synthea-specific; document for Day 17.

- Status field: only 'active' (30K) and 'completed' (544K) appear.
  No stopped/cancelled/on-hold/error. Simpler than real EHRs.

- DEFERRED: bronze_observations + observation parser moved to Day 12.
  Day 11 absorbed the 1-2 hour parser fix; observations was the right
  thing to push.
  ## Day 12 — silver_observations + Synthea data quality findings

### Parser work
- Extended fhir_parser to handle Observation resources. Four FHIR value shapes:
  valueQuantity (numeric labs/vitals), valueCodeableConcept (categorical like
  smoking status), valueString (free-text narratives), and component arrays
  (multi-part observations like blood pressure).
- Storage strategy: blood pressure split into 2 rows (one per LOINC component)
  rather than 1 row with separate systolic/diastolic columns. Uniform schema,
  industry-standard pattern (OMOP CDM). Slight row inflation accepted.
- PRAPARE social-determinants survey caught a parser bug on first pass:
  components with valueCodeableConcept (not valueQuantity) were returning
  empty rows. Extended component handler to cover all 4 value shapes inside
  components. Empty count dropped 210 -> 0.
- Same source-of-truth pattern as Day 11 medication fix: parse_all_bundles.py
  hardcoded 4 resource accumulators and silently dropped observations.
  Refactored to derive accumulators from RESOURCE_EXTRACTORS dict so adding
  a parser later flows through automatically.

### Schema work
- Bronze CREATE statements were never in version control (Day 5 ran them
  interactively in DuckDB). Codified all 5 Bronze tables into
  data_engineering/schema/bronze_schema.sql with IF NOT EXISTS so it's
  idempotent. Schema is now reproducible from a fresh clone.
- bronze_observations: 8,348,416 rows. 8.3M observations across 11,446
  patients (~730 per patient).

### Silver work
- silver_observations adds observation_kind (high-level bucket like hba1c,
  blood_pressure, weight, lab_other), is_critical_value (clinical thresholds
  for Day 19 anomaly detection), is_plausible_value (data-quality flag for
  biologically impossible Synthea values).

### Synthea data quality findings (THIS IS THE STORY)
- HbA1c: 49% of readings (44,108 of 90,453) are below 4.0%, which is
  clinically impossible (incompatible with life). Population avg before
  filter was 3.72%; after filter 5.9%. Realistic.
- HbA1c for T2DM patients (post-filter): avg 5.6%. LOWER than the population
  average. Real-world T2DM HbA1c averages 7-8%. Synthea is not generating
  clinically diabetic values for diabetic patients.
- BP for HTN patients: avg systolic 116.7. BP for non-HTN patients: avg
  116.6. Zero clinical difference between diagnosed hypertensives and
  controls. Real-world gap would be 15-25 mmHg.
- CONCLUSION: Synthea assigns condition diagnoses without linking them to
  realistic observation values. This is a known limitation of Synthea's
  underlying clinical model.

### Implications for Day 17
- The original adherence story ("did metformin lower HbA1c?", "did
  antihypertensives lower BP?") will NOT work on Synthea data.
- Pivot to prescription-pattern adherence:
    1. Treatment rate per condition (fill ratio) -- already computed
    2. Persistence (days from first to last prescription)
    3. Coverage gaps (intervals between consecutive prescriptions)
- This is closer to industry-standard "Proportion of Days Covered" (PDC)
  anyway. Real medication adherence research uses prescription data when
  lab data is sparse or unreliable.
- Document the finding in design_decisions.md as DD-002.

### Interview talking point
"I caught a clinically impossible 49% of HbA1c readings in the dataset.
Rather than silently filter them out, I added an is_plausible_value flag
so analytics can opt in. Then I dug deeper and found Synthea doesn't
link diagnoses to vital signs in a clinically realistic way -- which
means the original medication adherence story using clinical outcomes
wouldn't work, so I pivoted to prescription-pattern adherence (proportion
of days covered) which is the industry-standard approach anyway."
## Day 13 - dbt schema docs + Python validation suite

- 33 dbt tests across 5 silver models (unique, not_null, accepted_values,
  relationships). All FK tests against silver_patients pass — no orphan
  patient_ids in any child table. De-risks Week 4 Neo4j ingestion.
- Generic test arguments now nest under `arguments:` per dbt 1.11 deprecation.
  Took 3 iterations to catch them all because deprecation summary doesn't
  list every file unless --show-all-deprecations is passed.
- tests/validate_silver.py encodes the DD-001 and DD-002 quantitative claims
  as Python assertions. dbt tests catch schema bugs (a value outside the
  enum, a NULL where there shouldn't be one). They cannot catch
  distribution drift (33% disorder share, 49% HbA1c implausibility,
  67% T2DM treatment rate). The Python suite is the layer that does.
- Source-of-truth principle: every number in LEARNINGS.md should be
  reproducible from the data. If I claim 49% HbA1c implausibility, the
  validation script proves it. Anti-pattern: documenting numbers no test
  enforces — they go stale silently.
  ## Day 14
  - README restructured; DD-002 promoted to own section.
- DuckDB rationale reframed as trade-off, not workaround.
- Quickstart added — verified commands run from a fresh clone (if you actually verified this; if not, note that it's untested).
- Decision on the `is_billable_diagnosis` enforcement question (whatever you chose in Task 8).
- LinkedIn draft written, publishing Monday.
- Week 3 pre-flight (see Task 11).
## Day 15 pre-flight :
- 12,223 inpatient encounters across 4,609 patients (40% of cohort)
- 2.65 avg inpatient stays per inpatient patient
- 2,099 patients qualify as readmission candidates (2+ inpatient stays)
- Sufficient signal for 30-day window analysis — no rescoping needed.

Gotcha caught: initial preflight query nested COUNT(*) inside a
patient-level GROUP BY, so the outer count returned patients, not
encounters. Day 10's 12,223 figure was correct. Simpler query
(COUNT(*) with WHERE clause, no subquery) is the right pattern.
## Day 15 — gold_readmissions (CMS-aligned)

- Built gold_readmissions with LAG-based window function pairing.
  4,860 total pairs after excluding overlaps and >365-day gaps.
- Three orthogonal filters uncovered during sanity-checking:
    1. Overlap exclusion (days_between >= 0) — Synthea generates concurrent
       long-stay + acute encounters. Would have shipped a min_days = -5 bug.
    2. Planned admission exclusion (TNM staging, chemotherapy, therapy regimens)
       — Synthea codes recurring oncology follow-ups as inpatient encounters.
    3. Clinical reason exclusion (readmission_reason_is_clinical) — Synthea uses
       history codes and procedure codes as encounter reasons (CABG history,
       "patient transfer to SNF"). These dominated the pre-filter top 10.

- Rate progression through the filters:
    Raw:                            45.23% (4,860 pairs)
    Unplanned only:                 58.47% (1,382 pairs)   ← worse, exposed filter 3 was needed
    Clinical + unplanned:           19.34%  (543 pairs)    ← real signal
    Real-world CMS benchmark:      ~15%

- Post-filter top-10 finally looks clinically plausible: heart failure (#1),
  COVID-19 (#2), MI, aortic valve disease, drug abuse. Real acute drivers.

- Aggregation-bug caught during preflight: nested COUNT(*) inside a
  patient-level GROUP BY returns patient count, not encounter count.
  Same 4,609 for total_inpatient and patients_with_inpatient was the tell.

- DD-003 candidate confirmed: SNOMED suffix classification is not local to
  silver_conditions. It needs to be applied anywhere Synthea uses SNOMED:
  condition codes (DD-001), encounter reasons (Day 15), and likely
  observation categories (Day 18 utilization work).
  ## Day 16 — gold_chronic_conditions

- Grain: one row per (patient_id, condition_flag) pair. Diabetes + HTN
  patient = 2 rows. Downstream cohort filters trivial.
- Patient counts match Day 10 Silver exactly: 1,731 / 2,665 / 321 / 164.
  DD-001 filter (condition_flag IS NOT NULL) doing real work.
- Max comorbidity_count = 3. No patient in cohort has all 4 chronic flags.
- Prevalence vs real-world US benchmarks:
    Diabetes T2:    15.1% cohort  vs 11.6% US adults (slightly high — MA over-samples older)
    Hypertension:   23.3% cohort  vs 47%   US adults (Synthea under-diagnoses)
    Heart failure:   2.8% cohort  vs 2.4%  US adults (aligned)
    COPD:            1.4% cohort  vs 6.4%  US adults 40+ (Synthea under-diagnoses)
  HTN and COPD gaps are Synthea limitations, not model bugs. Log; don't fix.

- dbt-utils not installed. (patient_id, condition_flag) uniqueness enforced
  in tests/validate_gold.py instead. Day 21 cleanup: install dbt-utils.

- validate_gold.py added — same DD-002/DD-003 pattern as validate_silver.py.
  Every quantitative claim in LEARNINGS.md now has an assertion behind it.
  ## Day 17 — gold_medication_adherence with DD-004

- Built PDC (Proportion of Days Covered) over CMS-standard 365-day window
  ending at last_prescription. 8,546 patient-drug_class pairs across four
  chronic drug classes.
- First attempt used lifetime measurement period. Diagnosed as buggy,
  rewrote to 365-day window. Rebuild changed almost nothing (diff <= 0.026
  vs lifetime). My diagnosis was wrong — the problem was one layer deeper.
- Real finding: PDC is bimodal on Synthea data.
    64% of pairs: PDC < 0.25
    20% of pairs: PDC >= 0.80
    5% in the 0.50-0.80 middle range (real EHR distribution would center here)
- Root cause: Synthea emits MedicationRequest (prescriptions) but not
  MedicationDispense (pharmacy fills). Real patients on chronic drugs
  generate ~12 fills/year even without physician visits. Synthea generates
  one MedicationRequest per encounter, which may be years apart. This is
  correct FHIR behavior but breaks PDC.
- Persistence saves the story: median HTN patient has 3,612 days (9.9 years)
  between first and last prescription. 91% of HTN pairs persist >= 1 year.
  Persistence is what Synthea models well; PDC is what it doesn't.
- DD-004 documents the finding: PDC ships as informational, persistence is
  the operative signal. Do not invent synthetic fills to make PDC look
  better — that would defeat the point of catching the limitation.
- Refactored gold_medication_adherence to expose both pdc and persistence_days
  as first-class metrics. validate_gold.py enforces the DD-004 bimodal
  distribution so any future silent drift fails loudly.
- Interview talking point: "PDC on Synthea is 20-40% adherent depending on
  class. Real-world published PDC is 60-70%. Investigating the gap
  surfaced that Synthea models prescriptions but not pharmacy fills.
  This is the third Synthea limitation I've documented (DD-002 HbA1c,
  DD-003 SNOMED noise across resources, DD-004 no fill records). The
  pattern is: Synthea is excellent at encounters and diagnoses, weak on
  observations and dispensing. Portfolio projects that don't validate
  against real-world benchmarks miss this."
  ## Day 18 — gold_utilization + gold_provider_volume

- Two simple aggregation Gold models. 27 dbt tests + 6 validate_gold
  assertions, all passing.
- Cross-layer reconciliation exact:
    11,446 patients (Silver) -> 11,446 rows (gold_utilization)
    669,189 encounters (Silver) -> 669,189 (sum in gold_utilization)
    1,089 providers (Silver) -> 1,089 rows (gold_provider_volume)
- Top provider volume: 20,713 encounters / 2,469 unique patients.
  Long tail: median provider = 117 encounters.
- Cost NOT modeled. Synthea generates fabricated CPT-rate costs that don't
  reflect real payer contracts. Length of stay + duration used as
  utilization proxies. Optional Week 4+ extension: parse Encounter.cost.
- top_clinical_category column is uninformative: 69.5% "disorder", 30.5%
  NULL. All billable diagnoses classify to "disorder" in Synthea, so the
  column adds no signal. Documented in schema.yml. Day 21 cleanup: upgrade
  to top_clinical_subcategory if kept.
- No new DDs. Aggregations produced expected results.
- Zero-encounter patients: 0. Every Synthea patient has at least one
  encounter (birth encounter minimum). LEFT JOIN preserved but not needed.
  - top_clinical_category upgrade attempted (would show cardiac/endocrine/
  respiratory instead of uniform "disorder"). Blocked: silver_conditions
  doesn't have clinical_subcategory column. DD-001 stopped at the
  category level (disorder/finding/situation). Adding subcategory would
  require rebuilding Silver, which touches Day 10 work.
- Day 21 cleanup option: add clinical_subcategory to silver_conditions
  via SNOMED-code-to-body-system lookup, then rerun gold_provider_volume.
- Not doing tonight. Scope creep.
## Day 19 — Anomaly framework design + baseline analysis

- Designed 4 anomaly types in docs/anomaly_framework.md.
- Measured baselines pre-injection to size injected volumes correctly.
- Baselines:
    Warfarin + NSAID/aspirin: 11 (ship, ratio 3:1)
    HF 7-day readmission: 5 (ship, ratio 5:1)
    Chronic-drug persistence gap: 2,433 refined (drop, ratio 1:60)
    Post-discharge no-fill: 470 refined (drop, ratio 1:19)
- DD-005: dropped 2 anomaly types. DD-004 root cause. Both measure
  absence of prescriptions, which Synthea doesn't model reliably.
- silver_medications.drug_class taxonomy gap discovered: no anticoagulant
  or antiplatelet classes. Warfarin sits under 'other'. Aspirin sits
  under 'other'. Day 21 cleanup: add these classes and rebuild silver.
  For Day 20 anomaly detection, using medication_display ilike instead.
- gold.ground_truth_anomalies table designed but NOT created. Day 20 job.
- Interview point: baselines revealed the framework had to shrink from
  4 anomalies to 2 to produce measurable precision/recall. Honest scope
  reset beats padded scope. Ships 2 with 3-5x signal:noise > 4 with poor.
- Zero injector code today. Design first, code Day 20.
## Day 20 — Anomaly injector implementation

- Created gold.ground_truth_anomalies table via data_engineering/schema/gold_schema.sql.
- Wrote data_generation/anomaly_injector.py with two injector functions:
    inject_warfarin_coprescription: 30 patients, warfarin + aspirin/ibuprofen
    inject_hf_early_readmission:    25 patients, new inpatient encounter + matching Condition row
- Both injectors:
    - attach to REAL existing encounters (warfarin uses patient's most recent AMB)
    - use realistic RxNorm codes and SNOMED codes sampled from actual Silver rows
    - use IDs prefixed 'anomaly_' so they're greppable but FHIR-valid VARCHAR
    - are idempotent via delete-where-load_batch_id before insert
    - run inside a single transaction with rollback on failure
- HF injector also inserts matching silver_conditions row so downstream joins
  (gold_provider_volume.top_clinical_category etc.) remain consistent.
- Reproducibility: random.seed(20260720) locked so same patients selected on rerun.
- Post-injection counts match framework predictions EXACTLY:
    Warfarin detection: 11 baseline + 30 injected = 41 (matches)
    HF 7-day detection: 5 baseline + 25 injected = 30 (matches)
- validate_gold.py now has 6 more assertions locking in post-injection counts.
  Any silent Silver rebuild that drops injected rows fails loudly.
- DeprecationWarning on datetime.utcnow() — cleanup task for Day 21.
## Week 3 close (Day 21)

Delivered:
- 5 Gold models: readmissions, chronic_conditions, medication_adherence,
  utilization, provider_volume
- 3 new DDs: DD-003 (SNOMED classification crosses resource boundaries),
  DD-004 (Synthea has no MedicationDispense stream), DD-005 (2 of 4
  anomaly types dropped after baseline analysis)
- Anomaly injection framework: 30 warfarin + 25 HF injected. Post-injection
  detection = 41 and 30, matching baseline + injected exactly.
- validate_gold.py: 34 assertions locking every quantitative claim
- Test counts: 37 Silver dbt + 70 Gold dbt + 34 Python distribution
- Cross-layer reconciliation exact: 11,446 patients / 669,189 encounters
  / 1,089 providers all trace Silver → Gold with zero drift

Story arc:
- Week 1 established Bronze + FHIR literacy
- Week 2 established Silver + first two DD findings (DD-001 SNOMED noise,
  DD-002 broken observation values)
- Week 3 established Gold + three more DD findings, all rooted in Synthea
  data-generation limitations rather than model bugs

Interview through-line: 5 documented Synthea limitations + 5 Gold models
that gracefully degrade in the presence of those limitations. That's the
"safety-first" engineering the project claimed from Day 1. Now backed by
artifacts.

Week 3 gotchas surfaced and logged:
- silver_medications.drug_class taxonomy missing anticoagulant and
  antiplatelet classes. Warfarin currently sits under 'other'. Detection
  works via medication_display but the taxonomy gap is real.
- gold_provider_volume.top_clinical_category is uniform (all 'disorder').
  Would need clinical_subcategory on silver_conditions to produce signal.
- DeprecationWarning on datetime.utcnow() in anomaly_injector.py — trivial
  fix, deferred.

All three logged in README "Cleanup backlog" section for Week 4+.

Ready for Week 4 (Neo4j). Framework:
- Nodes: Patient, Encounter, Condition, Medication, Provider
- Edges: HAS_ENCOUNTER, DIAGNOSED_WITH, PRESCRIBED, OBSERVED, TREATED_BY
- Ingestion from Silver (not Gold — graph queries need atomic clinical events,
  not aggregations)
- Neo4j Aura free tier

## Day 22 — Neo4j Docker setup + graph schema design

- Chose Neo4j Community Edition 5.26 in Docker over Aura free tier. Aura
  limits to 50K nodes / 175K relationships — our graph needs ~682K nodes
  and ~2.3M relationships. Same reasoning as DuckDB: own the infrastructure,
  no expiry, always available for demo.
- docker-compose.yml at project root. Neo4j on bolt://localhost:7687,
  browser at http://localhost:7474. APOC plugin included.
- Memory allocation: 512MB heap initial, 1GB heap max, 512MB page cache.
  Sufficient for 682K nodes. Production would tune based on working set.
- Graph schema designed and documented in docs/graph_schema.md. Key design
  decisions:
    - Condition nodes deduplicated by snomed_code (308 unique codes from 414K
      condition rows). Medication nodes by rxnorm_code (352 unique).
    - Per-encounter context (onset_date, authored_on, status) lives on
      relationships, not nodes. Nodes hold the clinical concept.
    - Observations deferred — DD-002 confirmed data quality issues. 8.3M
      rows of unreliable data would inflate the graph 10x for no analytical
      gain.
    - Provider nodes are ID-only. silver_encounters has provider_id but no
      name or speciality columns. Synthea practitioner details live in
      separate FHIR bundles not parsed on Day 3.
- 5 uniqueness constraints + 9 query-performance indexes created before
  ingestion. MERGE requires unique constraints for idempotent upserts.
- Smoke test (create/read/delete test node) passed.
- GOTCHA: DuckDB file is mediquery.duckdb at project root (1.38 GB),
  NOT data_engineering/dbt/mediquery.duckdb (12 KB empty shell). The dbt
  folder's copy is what dbt creates by default. All ingestion scripts must
  point to the root-level file.
- Repo cleanup: moved ~15 investigation scripts from root into scripts/,
  deleted accidental files (play, ql(•••). Added dbt/target/, dbt/logs/,
  and __pycache__/ to .gitignore.

## Day 23 — Patient + Encounter ingestion into Neo4j

- Ingested 11,446 Patient nodes + 669,214 Encounter nodes + 669,214
  HAS_ENCOUNTER relationships. Total: 680,660 nodes, 669,214 rels.
- Patient ingestion: 5.2 seconds. Encounter ingestion: 65.6 seconds.
  Batched UNWIND with 5,000 rows per transaction.
- GOTCHA: Neo4j's date() function rejects ISO timestamps with time
  component. DuckDB birth_date comes through pandas as
  "2021-07-23T00:00:00" which Neo4j can't parse as Date. Fixed by
  truncating to first 10 chars (YYYY-MM-DD) before sending.
  birth_date is the only DATE column — all other temporal fields are
  TIMESTAMP (start_time, end_time, onset_date) which Neo4j handles
  natively as datetime.
- Encounter ingestion uses combined MATCH+MERGE pattern: MATCH the
  Patient (uses unique constraint index, O(1)), MERGE the Encounter,
  MERGE the HAS_ENCOUNTER relationship. One pass creates both nodes
  and relationships — fewer transactions than separating them.
- Zero orphan encounters (no patient_id mismatches). Day 13 dbt FK
  tests already guaranteed this, but verified in the graph anyway.
- Post-injection encounter count (669,214) reflects Day 20 anomaly
  injections (+25 HF readmission encounters over pre-injection 669,189).
  Injected data flows through automatically — no special handling.

## Day 24 — Condition, Medication, Provider nodes + 3 relationship types

### Node ingestion
- 308 Condition nodes (unique snomed_code), 352 Medication nodes (unique
  rxnorm_code), 1,089 Provider nodes. All three phases under 1 second each.
- Medication DISTINCT query returned 369 unique (rxnorm_code, medication_display,
  drug_class, medication_flag) tuples, but MERGE on rxnorm_code collapsed to
  352 nodes. Finding: 17 RxNorm codes map to multiple display/class combinations
  in Synthea data. Last-write-wins on SET properties. Not a bug — MERGE is
  working correctly — but worth investigating whether these are true drug
  synonyms or Synthea data inconsistencies. Logged for Day 25 or later.

### Relationship ingestion
- DIAGNOSED_WITH: 414,876 source rows → 414,876 relationships. No dedup
  occurred — every (encounter_id, snomed_code, condition_id) combination
  is unique. 79.6 seconds.
- PRESCRIBED: 574,888 source rows → 526,898 relationships. ~48K rows shared
  the same (encounter_id, rxnorm_code) pair, meaning the same drug was
  prescribed multiple times in one encounter via separate MedicationRequests.
  MERGE collapsed these. medication_request_id on the relationship stores
  only the last one per pair — minor data loss, acceptable for GraphRAG
  queries. 76.3 seconds.
- TREATED_BY: 669,214 source rows → 669,214 relationships. Exact match.
  One provider per encounter, no dedup. 48.6 seconds.

### Final graph stats
- Total: 682,409 nodes, 2,280,202 relationships
- Node breakdown: 669,214 Encounter | 11,446 Patient | 1,089 Provider |
  352 Medication | 308 Condition
- Relationship breakdown: 669,214 HAS_ENCOUNTER | 669,214 TREATED_BY |
  526,898 PRESCRIBED | 414,876 DIAGNOSED_WITH
- Ingestion time: ~5 minutes total across all phases
- Cross-layer reconciliation: Patient count (11,446), Encounter count
  (669,214), Provider count (1,089), Condition unique codes (308) all
  match DuckDB Silver exactly. PRESCRIBED dedup (574K → 527K) is the
  only expected discrepancy.
  ## Day 25 — HAS_CONDITION + anomaly verification + graph validation

### HAS_CONDITION relationships
- 225,912 aggregated (Patient)-[:HAS_CONDITION]->(Condition) relationships
  created from GROUP BY (patient_id, snomed_code) on silver_conditions.
  Each relationship carries first_onset, latest_onset, episode_count.
- Chronic cohort counts via HAS_CONDITION match Gold exactly:
  hypertension 2,665 | diabetes_t2 1,731 | heart_failure 321 | copd 164.
  Graph-based cohort queries now bypass Encounter traversal entirely.

### Anomaly verification
- All 55 injected anomalies present in the graph:
  25 encounter nodes (anomaly_* prefix), 60 prescription rels, 25 condition rels.
- Warfarin co-prescription query returned 72 patients, not 41. Root cause:
  Cypher query matches warfarin + NSAID across ANY encounters in patient history.
  A patient with aspirin in 2015 and warfarin in 2024 gets flagged. The SQL-based
  ground truth uses concurrent/overlapping prescriptions. Naive graph traversal
  inflates the cohort — same problem DD-001 identified for conditions now
  appearing in Cypher queries. Temporal constraints needed in Phase 3.
- HF 7-day readmissions returned 384, not ~30. Same root cause: Cypher query
  lacks the three Gold-model filters (overlap exclusion, planned admission
  exclusion, clinical reason exclusion). 384 is the raw unfiltered count,
  consistent with Day 15's 45% raw rate before filtering.
- Neither is an ingestion bug. Both are query refinement tasks for Day 38-40
  when the GraphRAG agent builds clinically-filtered Cypher.

### Cross-layer validation (all PASS)
- Patients: 11,446 | Encounters: 669,214 | Providers: 1,089 |
  Conditions: 308 | Medications: 352 — all match DuckDB Silver exactly.
- All four chronic cohorts match Gold: HTN 2,665, T2DM 1,731, HF 321, COPD 164.
- Inpatient encounters: 12,248 (includes 25 injected).
- DD-001 distribution at the node level: 214 disorder, 67 finding, 22 situation,
  5 unknown out of 308 unique SNOMED codes. Note: this is code-level, not
  row-level — the 33%/45%/22% split from Day 10 is weighted by frequency.

### Final graph stats
- 682,409 nodes | 2,506,114 relationships
- Relationship breakdown: 669,214 HAS_ENCOUNTER | 669,214 TREATED_BY |
  526,898 PRESCRIBED | 414,876 DIAGNOSED_WITH | 225,912 HAS_CONDITION
- Graph ingestion complete (Days 22-25). Phase 1 of the roadmap done.
- Total ingestion time across 4 days: ~5 minutes of compute.
  Development time was in schema design, validation, and debugging.
## Day 26 — Patient summary generation

- Generated 11,446 template-based patient summaries from DuckDB Silver.
  Template combines demographics, conditions, medications, and encounter
  history into natural language text. DD-007: template-based over LLM-generated
  — deterministic, free, reproducible.
- Summary stats: min 100 chars, median 789, max 1,793, mean 775.
  Summaries are dense enough for semantic search but short enough to
  embed efficiently.
- 247 patients have no medications (11,446 patients vs 11,199 medication
  aggregates). These are likely pediatric patients with encounters but
  no prescriptions. LEFT JOIN preserves them with NULL medication fields.
- Condition list truncated to top 8 disorders per patient to keep summaries
  focused. Full list available via graph traversal.
- Output: data_generation/parsed/patient_summaries.parquet (2.3 MB).
- GOTCHA: pandas NA doesn't support Python `or` operator. `row.get("age_at_death")
  or row.get("age_years_current")` throws "boolean value of NA is ambiguous."
  Fix: explicit `pd.notna()` check. Same class of bug as the Neo4j date()
  parsing issue on Day 23 — integration seams between libraries surface
  type mismatches.

## Day 27 — Chroma vector store

- Embedded 11,446 patient summaries into Chroma using all-MiniLM-L6-v2
  (384-dimensional embeddings, ~90 MB model, runs locally, no API key).
- Embedding took 288.7 seconds (~5 minutes) for 11K documents. Batch
  size 500. Persistent storage at chroma_db/ (gitignored).
- Semantic search verification with 5 test queries:
    - "elderly diabetic with heart problems" → returned patients with
      chronic conditions and cardiac history. Distances 0.40-0.43. Good.
    - "young patient with frequent emergency visits" → returned 1-4 year
      olds with ER encounters. Good.
    - "multiple chronic conditions and many medications" → returned patients
      with 4+ conditions and polypharmacy. Good.
    - "deceased patient with cancer history" → returned deceased infants,
      not cancer patients. Model weighted "deceased" over "cancer." Weak
      result but acceptable — cancer-specific queries route to Cypher
      (structured), not vector search.
    - "hypertension and kidney disease" → returned older patients with
      HTN and renal diagnoses. Good.
- GOTCHA: Chroma raises `NotFoundError` not `ValueError` when deleting
  a non-existent collection. Catch `Exception` broadly for idempotency.
- HuggingFace symlink warning on Windows is cosmetic — caching works,
  just uses more disk space. Can suppress with HF_HUB_DISABLE_SYMLINKS_WARNING.
  ## Day 28 — Query router

- Built regex-based query router that classifies NL queries into four
  categories: structured (→ Cypher), semantic (→ Chroma), hybrid (→ both),
  off_topic (→ refuse).
- 22-query test suite: 20/21 pass (95%). Single mismatch is "Tell me about
  diabetes" classified as hybrid instead of structured — genuinely ambiguous,
  not worth special-casing.
- Router uses additive confidence scoring: multiple matching patterns increase
  confidence. A query matching "how many" + "diabetes" + "over 65" scores
  0.9 structured vs a single keyword match at 0.2.
- Design decision: default to structured when no signals match. Cypher returns
  empty results gracefully; Chroma returns irrelevant results. Structured
  fail-closed is safer for the citation system.
- Off-topic detection catches prompt injection ("ignore previous instructions")
  at conf=1.3. Basic but sufficient — the FastAPI RBAC layer (Phase 4) is the
  real security boundary.
- No LLM calls in the router. Phase 3's LangChain agent imports classify_query()
  as a pre-filter but can override the decision with full conversational context.
- Phase 2 complete. Three days ahead of roadmap schedule (finished Day 28,
  roadmap had Phase 2 through Day 32).
  ## Day 29 — Ollama setup + Cypher generation quality test

- Installed Ollama with qwen2.5-coder:7b (4.7GB, fits in 14GB RAM alongside
  Docker/Neo4j). Code-specialized model chosen over general-purpose because
  Cypher is structured like SQL.
- DD-006 decided: Ollama local over OpenAI/Claude API. Same philosophy as
  DuckDB and Docker Neo4j — free, no expiry, fully offline demo. Tradeoff
  is weaker Cypher generation requiring more few-shot examples.
- Cypher quality test results:
    - WITHOUT schema: completely wrong. Hallucinated property names
      ({name: 'Diabetes'}), nonexistent relationship types (INPATIENT_ENCOUNTER),
      and referenced undefined variables. Unusable.
    - WITH schema injected: valid, executable Cypher on first try. Correct
      condition_flag value, correct is_inpatient filter, proper WITH/count/WHERE
      aggregation pattern. Only cosmetic issue (unused variable alias).
- Key finding: schema injection is the critical factor, not model size.
  A 7B model with the right schema outperforms a hypothetical larger model
  without it. Phase 3 agent MUST inject the full graph schema into every
  prompt — this is non-negotiable.
- Few-shot examples still needed for complex patterns: temporal windows
  (readmission within 30 days), multi-hop joins (patient → encounter →
  medication + condition), and anomaly detection queries. Simple count/filter
  queries work zero-shot with schema injection.
- langchain-ollama installed for Phase 3 integration.
## Day 30 — GraphRAG agent: basic NL → Cypher → answer pipeline

- Built two-step LangChain chain: (1) LLM generates Cypher from question +
  schema, (2) execute against Neo4j, (3) LLM synthesizes answer from results.
- Schema injection is mandatory (DD-006 confirmed). Without schema, the 7B
  model hallucinated property names and relationship types. With schema,
  simple queries worked zero-shot.
- 8-query test suite: 5 passed, 1 partial, 2 failed.
  Failures: multi-hop path (Patient→Encounter→Medication), exact string
  match instead of CONTAINS, counting patients instead of encounters.
- Auto-retry mechanism: if Cypher fails, error is fed back to the LLM for
  correction. Up to 2 retries.
- Latency: 15-30 seconds per query (two LLM calls on CPU). Acceptable for
  a local demo — the "AI thinking" delay is expected by users.

## Day 31 — Few-shot examples fix all Cypher generation failures

- Added 10 few-shot Cypher examples targeting Day 30's failure patterns:
  multi-hop medication paths, CONTAINS for drug names, encounter counts
  vs patient counts, condition display vs condition_flag, temporal
  readmission queries, comorbidity queries.
- Reran same 8-query test suite: 8/8 pass. All three failures fixed.
- Key few-shot patterns that made the difference:
    1. Explicit PRESCRIBED path: Patient→Encounter→Medication (not Patient→Medication)
    2. CONTAINS for medication names (Synthea stores "Warfarin Sodium 5 MG
       Oral Tablet", not "warfarin")
    3. clinical_category = 'disorder' + display for general condition queries
       (condition_flag is NULL for most conditions)
- Few-shot examples stored in cypher_few_shots.py, imported by the agent.
  Adding a new example is one dict append — no prompt rewriting.
- DD-006 fully validated: 7B Ollama model + schema injection + 10 few-shot
  examples = 100% on basic query suite. Complex queries (temporal windows,
  anomaly detection) still untested — Day 38.
  ## Day 32 — Citation guards + confidence scoring

### Confidence scoring
- Every query scored 0-100 from four components: cypher_valid (30 pts),
  execution (30 pts), results (5-25 pts by count), retry_penalty (-10 each).
- Three tiers: high (>=70) → full answer, moderate (40-69) → answer with
  caveat, low (<40) → refuse with explanation.
- All 8 test queries scored 80-85/100 (high confidence). Results component
  gives 20 for 1-3 rows, 25 for 4+. A failed query with 2 retries would
  score 45 (moderate) — answers but warns.
- Refusal behavior is the differentiator. Most chatbots always answer.
  This agent says "I can't answer this reliably" when confidence is low.

### Citation guards
- Answer synthesis prompt mandates citations: every claim must reference
  specific IDs from the query results. Post-processing validates cited
  IDs against actual result set using UUID regex matching.
- Two citation categories: patient-level queries cite patient_ids,
  aggregate queries cite numbers (no ID attribution needed).
- GOTCHA: Neo4j returns dot-prefixed keys (p.patient_id) but citation
  validator looked for bare keys (patient_id). All valid citations were
  flagged as hallucinated. Fixed by stripping prefix with split(".")[-1].
- Post-fix: Query 3 → 5 cited, 5 valid, 0 hallucinated. Query 6 → 3
  cited, 3 valid, 0 hallucinated. Zero false positives.
- Citation validation catches LLM-fabricated IDs before they reach the
  user. In production, hallucinated citations would be stripped from the
  answer or trigger a confidence downgrade.