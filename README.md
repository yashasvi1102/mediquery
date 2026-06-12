# MediQuery

Clinical analytics platform with safety-first AI. Synthetic FHIR data flows through a DuckDB Medallion lakehouse, gets modeled as a Neo4j knowledge graph, and is queried through a GraphRAG agent with mandatory citation guards.

**Status:** In development (Week 1 of 6)
**Stack:** Synthea, Python, DuckDB, dbt, Neo4j, LangChain, Ollama, Streamlit

## Why this exists

Healthcare AI without citation guards hallucinates. This project demonstrates a production-pattern: every AI response must cite specific FHIR resource IDs, the system refuses low-confidence answers, and a multi-persona dashboard enforces role-based access.

## Architecture

Coming Week 2.
