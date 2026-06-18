-- bronze_schema.sql
--
-- Day 5: Bronze layer schema for MediQuery.
--
-- Medallion philosophy:
--   - Bronze = raw, append-only, no constraints. Same patient can appear
--     multiple times across load_batch_ids. History is preserved.
--   - Silver enforces business keys, deduplicates, standardizes types.
--   - Gold aggregates for analytics consumers.
--
-- This file is idempotent: re-running it does NOT drop existing data.
-- If you change a column type later, you must drop the table manually.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- =========================================================================
-- bronze_patients
-- =========================================================================
CREATE TABLE IF NOT EXISTS bronze.bronze_patients (
    patient_id        VARCHAR,
    given_name        VARCHAR,
    family_name       VARCHAR,
    gender            VARCHAR,
    birth_date        DATE,
    deceased_date     TIMESTAMP,
    marital_status    VARCHAR,    -- M/S/D/W/U per FHIR value set
    race              VARCHAR,
    ethnicity         VARCHAR,
    city              VARCHAR,
    state             VARCHAR,
    postal_code       VARCHAR,
    country           VARCHAR,
    -- audit
    load_timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id     VARCHAR     -- distinguishes load runs
);

-- =========================================================================
-- bronze_encounters
-- =========================================================================
CREATE TABLE IF NOT EXISTS bronze.bronze_encounters (
    encounter_id      VARCHAR,
    patient_id        VARCHAR,
    status            VARCHAR,
    class_code        VARCHAR,    -- AMB / IMP / EMER per FHIR v3 ActCode
    class_display     VARCHAR,    -- usually NULL in Synthea; Silver fills in
    type_code         VARCHAR,    -- SNOMED procedure code
    type_display      VARCHAR,
    reason_code       VARCHAR,
    reason_display    VARCHAR,
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    provider_id       VARCHAR,    -- normalized from conditional reference
    -- audit
    load_timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id     VARCHAR
);

-- =========================================================================
-- bronze_conditions
-- =========================================================================
-- NOTE: Synthea emits SNOMED only (not ICD-10).
-- Silver layer must filter non-disease findings (situation/procedure/finding
-- that's not a disorder) before Day 16's chronic-condition cohorts.
CREATE TABLE IF NOT EXISTS bronze.bronze_conditions (
    condition_id          VARCHAR,
    patient_id            VARCHAR,
    encounter_id          VARCHAR,
    code_system           VARCHAR,
    code                  VARCHAR,
    display               VARCHAR,
    clinical_status       VARCHAR,
    verification_status   VARCHAR,
    onset_date            TIMESTAMP,
    abatement_date        TIMESTAMP,
    recorded_date         TIMESTAMP,
    -- audit
    load_timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id         VARCHAR
);

-- =========================================================================
-- bronze_medication_requests
-- =========================================================================
CREATE TABLE IF NOT EXISTS bronze.bronze_medication_requests (
    medication_request_id  VARCHAR,
    patient_id             VARCHAR,
    encounter_id           VARCHAR,
    status                 VARCHAR,
    intent                 VARCHAR,
    code_system            VARCHAR,
    medication_code        VARCHAR,    -- RxNorm
    medication_display     VARCHAR,
    authored_on            TIMESTAMP,
    dosage_text            VARCHAR,
    -- audit
    load_timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id          VARCHAR
);

-- Future additions (when their parsers are written):
-- bronze.bronze_observations    -- Day 11
-- bronze.bronze_procedures      -- not in current plan, optional