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