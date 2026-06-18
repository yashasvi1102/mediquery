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