create schema if not exists gold;

create table if not exists gold.ground_truth_anomalies (
    anomaly_id          varchar    primary key,
    anomaly_type        varchar    not null,
    patient_id          varchar    not null,
    injection_timestamp timestamp  not null,
    injection_batch_id  varchar    not null,
    details             json,
    detected_by_agent   boolean    default false,
    detected_at         timestamp
);