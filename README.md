# MediQuery

**A safety-first healthcare data intelligence platform** that parses 11,446 synthetic FHIR patient records through a DuckDB Medallion architecture, loads them into a Neo4j knowledge graph, and exposes a GraphRAG agent with citation guards, confidence scoring, and 4-tier RBAC — all running locally with zero cloud dependencies.

## What Makes This Different

Most Synthea portfolio projects parse the data and build dashboards. This one interrogates the data.

**Five documented data quality findings** that most tutorials miss:

| Finding | Impact |
|---------|--------|
| **DD-001**: 67% of FHIR "conditions" are not diseases — they're social factors, admin events, and employment status | Naive cohort queries inflate by 3x without SNOMED classification |
| **DD-002**: 49% of HbA1c readings are clinically impossible (below 4.0%, incompatible with life) | Observation-based adherence metrics don't work on Synthea |
| **DD-003**: SNOMED noise appears in encounter reasons and observation categories, not just conditions | Classification must be applied project-wide, not per-table |
| **DD-004**: Synthea generates prescriptions but not pharmacy fills — PDC underestimates adherence by 30-50% | Pivoted from PDC to persistence-based adherence |
| **DD-005**: 2 of 4 anomaly types dropped after baseline analysis showed undetectable signal:noise ratios | Shipped 2 measurable anomalies instead of 4 noisy ones |

Two additional design decisions document the LLM and summary generation choices (DD-006, DD-007).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamlit UI (4 personas)                 │
│        Doctor │ Researcher │ Admin │ Patient                 │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP + JWT
┌──────────────────────▼──────────────────────────────────────┐
│              FastAPI RBAC Layer (port 8080)                  │
│   Token auth │ Role-based filtering │ Filter before synthesis│
└───────┬──────────────┬──────────────────┬───────────────────┘
        │              │                  │
┌───────▼───────┐ ┌────▼─────┐ ┌──────────▼──────────┐
│  Query Router │ │  Ollama  │ │  Confidence Scorer   │
│  (regex-based)│ │  LLM     │ │  + Citation Guards   │
│  structured/  │ │qwen2.5-  │ │  refuse < 40         │
│  semantic/    │ │coder:7b  │ │  caveat 40-69        │
│  hybrid       │ │          │ │  answer >= 70        │
└───────┬───────┘ └────┬─────┘ └──────────────────────┘
        │              │
┌───────▼───────┐ ┌────▼──────────┐ ┌─────────────────┐
│    Neo4j      │ │  Chroma       │ │    DuckDB        │
│  682K nodes   │ │  11K patient  │ │  Medallion       │
│  2.5M rels    │ │  embeddings   │ │  Bronze/Silver/  │
│  (Docker)     │ │  (local)      │ │  Gold + dbt      │
└───────────────┘ └───────────────┘ └─────────────────┘
```

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Data Generation | Synthea (11,446 MA patients) | Realistic FHIR bundles, free, reproducible |
| Warehouse | DuckDB | Free forever, no trial expiry, fast analytical queries |
| Transformation | dbt-duckdb | Medallion architecture, tested models, lineage |
| Knowledge Graph | Neo4j Community (Docker) | No Aura node limits (682K nodes vs 50K cap), local |
| Vector Store | Chroma + all-MiniLM-L6-v2 | Free, local embeddings, semantic patient search |
| LLM | Ollama (qwen2.5-coder:7b) | Free, offline, no API key, good Cypher generation with schema injection |
| API | FastAPI | JWT auth, RBAC enforcement, role-based data filtering |
| UI | Streamlit | 4-persona interface, chat, cohort builder, anomaly alerts |
| Testing | dbt tests + Python validation | 107 dbt tests + 34 Python assertions + 28 edge cases |

## Quickstart

**Prerequisites:** Python 3.13, Docker Desktop, Ollama, ~14GB RAM

```bash
# 1. Clone
git clone https://github.com/yashasvi1102/mediquery.git
cd mediquery

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 3. Pull LLM model
ollama pull qwen2.5-coder:7b

# 4. Start services
docker compose up -d             # Neo4j (wait 15 seconds)
uvicorn app.main:app --port 8080 # API (new terminal)
streamlit run streamlit_app.py   # UI (new terminal)

# 5. Open http://localhost:8501 and pick a role
```

**Note:** The first run requires Synthea data generation and pipeline execution (Days 1-21). The graph, Chroma, and DuckDB data are pre-populated if you're running from a complete build.

## RBAC Access Matrix

| Capability | Doctor | Researcher | Admin | Patient |
|-----------|--------|------------|-------|---------|
| Clinical queries (NL → Cypher) | ✓ full data | ✓ hashed IDs, no names | ✓ aggregates only | ✓ own record only |
| Cohort builder | ✓ | ✓ de-identified | ✗ (403) | ✗ (403) |
| Anomaly detection | ✓ | ✓ de-identified | ✗ (403) | ✗ (403) |
| See patient names | ✓ | ✗ stripped | ✗ | own only |
| See raw patient IDs | ✓ | ✗ hashed (P-xxxx) | ✗ | own only |
| Cypher visible | ✓ | ✓ | ✗ | ✗ |
| No auth | 401 | 401 | 401 | 401 |

RBAC is enforced at the API layer. Data is filtered **before** the LLM synthesizes the answer. Streamlit never sees restricted data.

## Anomaly Detection Benchmark

Two anomaly types with injected ground truth (30 warfarin + 25 HF):

| Anomaly | Detected | TP | FP (baseline) | FN | Precision | Recall |
|---------|----------|-----|---------------|-----|-----------|--------|
| Warfarin co-prescription | 72 | 30 | 42 | 0 | 41.7% | 100% |
| HF 7-day readmission | 155 | 25 | 130 | 0 | 16.1% | 100% |

**100% recall** — every injected anomaly found. Precision reflects lifetime co-occurrence (a patient who took aspirin in 2015 and warfarin in 2024 gets flagged). Temporal concurrence filtering would improve precision but requires date-range logic on prescriptions.

## Graph Statistics

| Entity | Count |
|--------|-------|
| Patient nodes | 11,446 |
| Encounter nodes | 669,214 |
| Condition nodes (unique SNOMED) | 308 |
| Medication nodes (unique RxNorm) | 352 |
| Provider nodes | 1,089 |
| **Total nodes** | **682,409** |
| HAS_ENCOUNTER relationships | 669,214 |
| TREATED_BY relationships | 669,214 |
| PRESCRIBED relationships | 526,898 |
| DIAGNOSED_WITH relationships | 414,876 |
| HAS_CONDITION relationships | 225,912 |
| **Total relationships** | **2,506,114** |

## GraphRAG Agent Performance

| Metric | Result |
|--------|--------|
| Cypher generation accuracy | 8/8 on test suite (with 11 few-shot examples) |
| Query routing accuracy | 95% (20/21 test queries) |
| Citation validation | 0 hallucinated citations across all tests |
| Edge case handling | 0 crashes on 28 adversarial inputs |
| Confidence scoring | 80-85/100 on valid queries, refuses below 40 |
| Off-topic detection | Catches prompt injection, refuses gracefully |

## Project Structure

```
mediquery/
├── data_generation/           # Synthea parsing, anomaly injection
│   ├── fhir_parser.py         # FHIR bundle → structured records
│   ├── parse_all_bundles.py   # Batch parser (11,446 patients)
│   └── anomaly_injector.py    # Ground truth anomaly injection
├── data_engineering/
│   ├── dbt/                   # Medallion architecture (Silver/Gold)
│   ├── schema/                # Bronze + Gold SQL schemas
│   ├── neo4j/                 # Graph ingestion + GraphRAG agent
│   │   ├── graphrag_agent.py  # NL → Cypher → answer pipeline
│   │   ├── cohort_builder.py  # NL cohort definition → stats
│   │   ├── query_router.py    # Structured/semantic/hybrid routing
│   │   ├── cypher_few_shots.py # Few-shot examples for LLM
│   │   └── ingest_*.py        # Neo4j data loading scripts
│   └── load_bronze.py         # Parquet → DuckDB Bronze loader
├── app/                       # FastAPI RBAC application
│   ├── main.py                # API endpoints
│   ├── auth.py                # JWT token management
│   └── rbac.py                # Role-based data filtering
├── streamlit_app.py           # 4-persona UI
├── tests/                     # Validation suites
├── docs/                      # Design decisions, graph schema
├── docker-compose.yml         # Neo4j service
├── mediquery.duckdb           # Analytical warehouse (gitignored)
├── LEARNINGS.md               # Day-by-day build log
└── requirements.txt           # Python dependencies
```

## Design Decisions

All documented in [docs/design_decisions.md](docs/design_decisions.md):

- **DD-001**: SNOMED classification in Silver (67% non-disease filter)
- **DD-002**: Synthea observation values don't correlate with diagnoses
- **DD-003**: SNOMED noise crosses FHIR resource boundaries
- **DD-004**: Synthea has no MedicationDispense stream (PDC broken)
- **DD-005**: 2 of 4 anomaly types dropped after baseline analysis
- **DD-006**: Ollama local over cloud APIs (free, offline, no expiry)
- **DD-007**: Template-based patient summaries over LLM-generated

## What I'd Do Differently

- **Add temporal concurrence to anomaly detection.** Current warfarin query uses lifetime co-occurrence. Checking if both drugs were active in the same time window would improve precision from 42% to ~80%+.
- **Parse Synthea practitioner bundles.** Provider nodes are ID-only because the FHIR parser doesn't extract practitioner details from separate bundles. Enriching Provider nodes with name and speciality would improve the demo.
- **Use GPT-4o for Cypher generation.** The 7B local model works (8/8 test accuracy) but is slow (30-60s per query). GPT-4o would be 2-3 seconds with better accuracy on complex queries. The architecture supports swapping via environment variable.
- **Add clinical_subcategory to Silver conditions.** DD-001 classifies at the category level (disorder/finding/situation). Adding body-system subcategories (cardiac/respiratory/endocrine) would make provider analytics and cohort breakdowns more useful.

