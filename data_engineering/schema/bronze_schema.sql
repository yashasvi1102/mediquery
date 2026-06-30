-- ============================================================================
-- mediquery Bronze layer schema
-- ============================================================================
-- Raw FHIR data, append-only. No primary keys or unique constraints
-- at this layer (Medallion principle: Bronze preserves source-of-truth history).
-- Dedup, normalization, and classification happen in Silver.
--
-- Audit columns on every table:
--   load_timestamp -- when the row was inserted (defaults to CURRENT_TIMESTAMP)
--   load_batch_id  -- identifier per pipeline run, set by load_bronze.py
--
-- Run order:
--   1. CREATE SCHEMA statements
--   2. CREATE TABLE statements
--
-- Idempotent: safe to re-run; uses IF NOT EXISTS.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- ----------------------------------------------------------------------------
-- bronze_patients
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.bronze_patients (
    patient_id      VARCHAR,
    given_name      VARCHAR,
    family_name     VARCHAR,
    gender          VARCHAR,
    birth_date      DATE,
    deceased_date   TIMESTAMP,
    marital_status  VARCHAR,
    race            VARCHAR,
    ethnicity       VARCHAR,
    city            VARCHAR,
    state           VARCHAR,
    postal_code     VARCHAR,
    country         VARCHAR,
    load_timestamp  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id   VARCHAR
);

-- ----------------------------------------------------------------------------
-- bronze_encounters
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.bronze_encounters (
    encounter_id    VARCHAR,
    patient_id      VARCHAR,
    status          VARCHAR,
    class_code      VARCHAR,
    class_display   VARCHAR,
    type_code       VARCHAR,
    type_display    VARCHAR,
    reason_code     VARCHAR,
    reason_display  VARCHAR,
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    provider_id     VARCHAR,
    load_timestamp  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id   VARCHAR
);

-- ----------------------------------------------------------------------------
-- bronze_conditions
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.bronze_conditions (
    condition_id         VARCHAR,
    patient_id           VARCHAR,
    encounter_id         VARCHAR,
    code_system          VARCHAR,
    code                 VARCHAR,
    display              VARCHAR,
    clinical_status      VARCHAR,
    verification_status  VARCHAR,
    onset_date           TIMESTAMP,
    abatement_date       TIMESTAMP,
    recorded_date        TIMESTAMP,
    load_timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id        VARCHAR
);

-- ----------------------------------------------------------------------------
-- bronze_medication_requests
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.bronze_medication_requests (
    medication_request_id  VARCHAR,
    patient_id             VARCHAR,
    encounter_id           VARCHAR,
    status                 VARCHAR,
    intent                 VARCHAR,
    code_system            VARCHAR,
    medication_code        VARCHAR,
    medication_display     VARCHAR,
    authored_on            TIMESTAMP,
    dosage_text            VARCHAR,
    load_timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id          VARCHAR
);

-- ----------------------------------------------------------------------------
-- bronze_observations  (Day 12)
-- ----------------------------------------------------------------------------
-- Observations span four FHIR value shapes (numeric labs/vitals, categorical
-- codes like smoking status, free-text narratives, and multi-component
-- panels like blood pressure). Storage strategy: one row per atomic
-- observation. Blood-pressure-style component arrays are split into one
-- row per component (so systolic and diastolic become separate rows, each
-- keyed by its own LOINC code).
--
-- value_numeric / value_text / value_code:
--   Exactly one is populated per row (numeric labs use value_numeric;
--   categorical observations use value_text + value_code; narratives use
--   value_text only).
--
-- parent_observation_id:
--   NULL for atomic observations. For component-split rows, points to
--   the FHIR Observation.id of the parent panel (e.g., the BP panel) so
--   downstream models can reconstruct the multi-component groupings if
--   needed.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.bronze_observations (
    observation_id         VARCHAR,
    parent_observation_id  VARCHAR,
    patient_id             VARCHAR,
    encounter_id           VARCHAR,
    status                 VARCHAR,
    category               VARCHAR,
    loinc_code             VARCHAR,
    loinc_display          VARCHAR,
    value_numeric          DOUBLE,
    value_text             VARCHAR,
    value_code             VARCHAR,
    value_code_system      VARCHAR,
    unit                   VARCHAR,
    effective_date         TIMESTAMP,
    issued_date            TIMESTAMP,
    load_timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    load_batch_id          VARCHAR
);
