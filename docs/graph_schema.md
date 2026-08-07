# MediQuery Graph Schema (Corrected — matches actual Silver columns)

## Design Principles

1. **Nodes represent entities, not rows.** Condition and Medication nodes are deduplicated
   by code. Silver has 414K condition rows but only ~300-500 unique SNOMED codes. The graph
   stores unique clinical concepts as nodes. Per-encounter context lives on relationships.

2. **Ingest from Silver, not Gold.** Gold tables are aggregations (readmission rates,
   utilization counts). The graph needs atomic clinical events to enable flexible traversal.

3. **Observations deferred.** 8.3M rows, and DD-002 confirmed Synthea doesn't link
   observations to diagnoses in a clinically realistic way. Extension candidate after
   core graph is validated.

4. **DuckDB path:** `mediquery.duckdb` at project root (NOT `data_engineering/dbt/mediquery.duckdb`
   which is an empty 12KB shell).

---

## Estimated Counts

| Entity             | Source                    | Estimated Count |
|--------------------|--------------------------|-----------------|
| Patient nodes      | silver_patients           | 11,446          |
| Encounter nodes    | silver_encounters         | 669,189         |
| Condition nodes    | DISTINCT snomed_code      | ~300-500        |
| Medication nodes   | DISTINCT rxnorm_code      | ~200-400        |
| Provider nodes     | DISTINCT provider_id      | 1,089           |
| **Total nodes**    |                           | **~682K**       |
| HAS_ENCOUNTER rels | silver_encounters         | 669,189         |
| DIAGNOSED_WITH rels| silver_conditions         | 414,851         |
| PRESCRIBED rels    | silver_medications        | 574,828         |
| TREATED_BY rels    | silver_encounters         | 669,189         |
| HAS_CONDITION rels | aggregated                | ~15K-30K        |
| **Total rels**     |                           | **~2.3M**       |

---

## Node Schemas

### (:Patient)

Source: `silver.silver_patients`

```
patient_id        VARCHAR   — PK, unique constraint
given_name        VARCHAR   — first name (for Doctor persona, stripped in Researcher view)
family_name       VARCHAR   — last name (same RBAC treatment)
gender            VARCHAR   — "male" / "female" (FHIR-native)
birth_date        DATE
is_deceased       BOOLEAN
deceased_date     TIMESTAMP — NULL if alive
age_years_current BIGINT    — non-deterministic helper
age_at_death      BIGINT    — NULL if alive
age_group         VARCHAR   — bucketed age range
marital_status    VARCHAR   — M/S/D/W/U
race              VARCHAR
ethnicity         VARCHAR
city              VARCHAR
state             VARCHAR
postal_code       VARCHAR
country           VARCHAR
```

### (:Encounter)

Source: `silver.silver_encounters`

```
encounter_id          VARCHAR   — PK, unique constraint
encounter_type        VARCHAR   — ambulatory/emergency/inpatient/home_health/virtual
is_inpatient          BOOLEAN
start_time            TIMESTAMP — NOTE: TIMESTAMP not DATE
end_time              TIMESTAMP
duration_minutes      BIGINT
length_of_stay_days   BIGINT
reason_code           VARCHAR   — SNOMED code for encounter reason (may be NULL)
reason_display        VARCHAR
type_code             VARCHAR   — encounter type SNOMED code
type_display          VARCHAR
```

Note: patient_id and provider_id are NOT properties on the Encounter node.
They are encoded as relationships (HAS_ENCOUNTER, TREATED_BY).

### (:Condition)

Source: `SELECT DISTINCT snomed_code, display, clinical_category, condition_flag, is_billable_diagnosis FROM silver.silver_conditions`

```
snomed_code           VARCHAR   — PK, unique constraint
display               VARCHAR   — human-readable name
clinical_category     VARCHAR   — disorder/finding/situation/unknown (DD-001)
condition_flag        VARCHAR   — diabetes_t2/hypertension/heart_failure/copd/NULL
is_billable_diagnosis BOOLEAN
```

### (:Medication)

Source: `SELECT DISTINCT rxnorm_code, medication_display, drug_class, medication_flag FROM silver.silver_medications`

```
rxnorm_code           VARCHAR   — PK, unique constraint
medication_display    VARCHAR
drug_class            VARCHAR   — biguanide/insulin/ace_inhibitor/statin/opioid/etc.
medication_flag       VARCHAR   — diabetes_drug/antihypertensive/heart_failure_drug/copd_drug/NULL
```

### (:Provider)

Source: `SELECT DISTINCT provider_id FROM silver.silver_encounters WHERE provider_id IS NOT NULL`

```
provider_id           VARCHAR   — PK, unique constraint
```

NOTE: silver_encounters only has provider_id — no provider_name or
provider_speciality columns. If gold_provider_volume has these fields,
we can enrich during ingestion. Otherwise Provider nodes are ID-only.
This is a Synthea limitation: practitioner details live in separate
FHIR bundles that weren't parsed (Day 3 notes this).

---

## Relationship Schemas

### (:Patient)-[:HAS_ENCOUNTER]->(:Encounter)

Source: `silver.silver_encounters` — one per encounter row.
No properties on this relationship.

### (:Encounter)-[:DIAGNOSED_WITH]->(:Condition)

Source: `silver.silver_conditions` — one per condition row.
Links encounter to the condition diagnosed during it.

```
condition_id          VARCHAR   — silver PK for traceability
onset_date            TIMESTAMP
abatement_date        TIMESTAMP — NULL if chronic/ongoing
clinical_status       VARCHAR
is_active             BOOLEAN
```

### (:Encounter)-[:PRESCRIBED]->(:Medication)

Source: `silver.silver_medications` — one per medication row.

```
medication_request_id VARCHAR   — silver PK for traceability
status                VARCHAR   — active/completed
authored_on           TIMESTAMP
```

### (:Encounter)-[:TREATED_BY]->(:Provider)

Source: `silver.silver_encounters` — one per encounter.
No additional properties.

### (:Patient)-[:HAS_CONDITION]->(:Condition)

Aggregated convenience relationship. Built by grouping
silver_conditions per (patient_id, snomed_code).

```
first_onset           TIMESTAMP
latest_onset          TIMESTAMP
episode_count         BIGINT
```

---

## Indexes and Constraints

```cypher
// Uniqueness constraints (also create implicit indexes)
CREATE CONSTRAINT patient_id_unique IF NOT EXISTS
  FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;

CREATE CONSTRAINT encounter_id_unique IF NOT EXISTS
  FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE;

CREATE CONSTRAINT snomed_code_unique IF NOT EXISTS
  FOR (c:Condition) REQUIRE c.snomed_code IS UNIQUE;

CREATE CONSTRAINT rxnorm_code_unique IF NOT EXISTS
  FOR (m:Medication) REQUIRE m.rxnorm_code IS UNIQUE;

CREATE CONSTRAINT provider_id_unique IF NOT EXISTS
  FOR (prov:Provider) REQUIRE prov.provider_id IS UNIQUE;

// Query-performance indexes
CREATE INDEX encounter_type_idx IF NOT EXISTS
  FOR (e:Encounter) ON (e.encounter_type);

CREATE INDEX encounter_inpatient_idx IF NOT EXISTS
  FOR (e:Encounter) ON (e.is_inpatient);

CREATE INDEX encounter_start_time_idx IF NOT EXISTS
  FOR (e:Encounter) ON (e.start_time);

CREATE INDEX condition_flag_idx IF NOT EXISTS
  FOR (c:Condition) ON (c.condition_flag);

CREATE INDEX condition_category_idx IF NOT EXISTS
  FOR (c:Condition) ON (c.clinical_category);

CREATE INDEX medication_flag_idx IF NOT EXISTS
  FOR (m:Medication) ON (m.medication_flag);

CREATE INDEX medication_class_idx IF NOT EXISTS
  FOR (m:Medication) ON (m.drug_class);

CREATE INDEX patient_gender_idx IF NOT EXISTS
  FOR (p:Patient) ON (p.gender);

CREATE INDEX patient_deceased_idx IF NOT EXISTS
  FOR (p:Patient) ON (p.is_deceased);
```

---

## Validation Queries (Day 26)

### Chronic condition patient counts (must match gold)
```cypher
MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition)
WHERE c.condition_flag IS NOT NULL
RETURN c.condition_flag AS flag, count(DISTINCT p) AS patient_count
ORDER BY patient_count DESC
// Expect: 1731 diabetes_t2, 2665 hypertension, 321 heart_failure, 164 copd
```

### 30-day readmission candidates
```cypher
MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter {is_inpatient: true})
MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter {is_inpatient: true})
WHERE e2.start_time > e1.end_time
  AND e2.start_time <= e1.end_time + duration({days: 30})
  AND e1.encounter_id <> e2.encounter_id
RETURN count(DISTINCT e2) AS readmissions_30d
```

### Warfarin co-prescription detection (anomaly framework)
```cypher
MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter)-[:PRESCRIBED]->(m1:Medication)
MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter)-[:PRESCRIBED]->(m2:Medication)
WHERE m1.medication_display CONTAINS 'Warfarin'
  AND (m2.medication_display CONTAINS 'Aspirin'
       OR m2.medication_display CONTAINS 'Ibuprofen')
RETURN DISTINCT p.patient_id
// Expect: 41 patients (11 baseline + 30 injected)
```

---

## What's NOT in the Graph (and Why)

| Omitted | Reason | Extension path |
|---------|--------|----------------|
| Observations | 8.3M rows, DD-002 quality issues | Add (:Observation) with is_plausible_value filter |
| Provider name/speciality | Not in silver_encounters columns | Parse practitioner FHIR bundles or enrich from gold_provider_volume |
| Cost data | Synthea costs are fabricated (Day 18) | Parse Encounter.cost if needed |
| clinical_subcategory | Doesn't exist in Silver yet (Day 18 blocker) | Add to silver_conditions, then propagate |
