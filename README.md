# MediQuery

Clinical analytics platform with safety-first AI. Synthetic FHIR data flows through a DuckDB Medallion lakehouse, gets modeled as a Neo4j knowledge graph, and is queried through a GraphRAG agent with mandatory citation guards.

**Status:** In development (Week 1 of 6)
**Stack:** Synthea, Python, DuckDB, dbt, Neo4j, LangChain, Ollama, Streamlit

## Why this exists

Healthcare AI without citation guards hallucinates. This project demonstrates a production-pattern: every AI response must cite specific FHIR resource IDs, the system refuses low-confidence answers, and a multi-persona dashboard enforces role-based access.

## Architecture

Coming Week 2.
# MediQuery

Clinical analytics platform with safety-first AI on synthetic FHIR data.

## Status

**Week 1 of 6 complete.** Bronze layer of a Medallion lakehouse loaded with 11,446 synthetic patients and 1.67M clinical records, parsed from raw FHIR JSON bundles.

## What's built so far

- Synthea generating 11,446 synthetic Massachusetts patient bundles
- FHIR parser extracting 4 resource types (Patient, Encounter, Condition, MedicationRequest) into typed Python dicts
- Batch driver scaling parser to all 11,446 bundles with error handling and parquet intermediate output (zero failures)
- DuckDB lakehouse with Bronze/Silver/Gold schemas and audit columns
- Bronze loader using DuckDB's native `read_parquet()` — loads 1.67M rows in ~5 seconds with self-verifying row counts

## Bronze layer row counts

| Table | Rows |
|---|---|
| bronze_patients | 11,446 |
| bronze_encounters | 669,189 |
| bronze_conditions | 414,851 |
| bronze_medication_requests | 574,828 |

![Bronze counts](docs/week1_bronze_counts.png)

## Architecture (planned)

[Insert the ASCII architecture diagram from MediQuery_Project_Documentation.md, but mark Week 1 components with ✅ and everything else with ⬜]

## Stack

- **Data generation:** Synthea (synthetic FHIR generator)
- **Parsing:** Python 3.13, fhir.resources, pandas + pyarrow
- **Warehouse:** DuckDB (Medallion architecture; SQL portable to Snowflake/BigQuery)
- **Transformations:** dbt (Week 2)
- **Knowledge graph:** Neo4j Aura (Week 4)
- **AI layer:** LangChain + Chroma (Week 5)
- **Dashboard:** Streamlit (Week 6)

## Why DuckDB instead of Snowflake

Snowflake's free trial expires after 30 days. This project is a 6-week build and needs to be demoable indefinitely afterward. DuckDB gives the same SQL surface, same dbt workflow, and zero ongoing cost. The Medallion architecture pattern is what matters for the resume — the engine is portable.

## Repo structure

[Run `tree /F /A` or copy from your folder structure — show what actually exists, don't list folders you haven't built yet]

## Roadmap

- ✅ Week 1: FHIR ingestion + Bronze layer
- ⬜ Week 2: dbt Silver transformations
- ⬜ Week 3: Gold clinical metrics
- ⬜ Week 4: Neo4j knowledge graph
- ⬜ Week 5: GraphRAG agent with citation guards
- ⬜ Week 6: Multi-persona dashboard + benchmarks
