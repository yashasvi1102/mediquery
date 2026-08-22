# Design Decisions

## DD-001: SNOMED hierarchy classification in Silver Conditions (Day 10)

**Problem found Day 7:** Top 10 conditions across 11,446 patients are 70% non-clinical:

| Rank | Condition | Count | Type |
|---|---|---|---|
| 1 | Medication review due (situation) | 82,171 | Administrative |
| 2 | Stress (finding) | 32,447 | SDOH |
| 3 | Gingivitis (disorder) | 30,703 | Clinical |
| 4 | Full-time employment (finding) | 29,885 | SDOH |
| 5 | Part-time employment (finding) | 18,574 | SDOH |
| 6 | Social isolation (finding) | 11,689 | SDOH |
| 7 | Viral sinusitis (disorder) | 11,631 | Clinical |
| 8 | Limited social contact (finding) | 11,561 | SDOH |
| 9 | Not in labor force (finding) | 10,431 | SDOH |
| 10 | Gingival disease (disorder) | 8,951 | Clinical |

**Decision:** Silver Conditions table will include three classification columns:
- `clinical_category` — disorder / finding / situation (from SNOMED suffix)
- `clinical_subcategory` — disease system if disorder, SDOH domain if finding
- `is_billable_diagnosis` — boolean

**Why this matters:** Any naive "find patients with conditions" query inflates cohorts. Hospital analytics and AI cohort builders need this filter or they produce wrong numbers.

**Where this shows up:**
- Day 10: Silver Conditions classification logic
- Day 12: dbt test that fails if SDOH leaks into disease-filter queries
- Day 26: Clinical query library uses `is_billable_diagnosis = true` by default
- Day 32: AI agent confidence drops if it queries without the filter
- Day 41: Blog post writes itself from this finding

## DD-002: Synthea Doesn't Link Diagnoses to Observation Values

**Date:** 2026-06-30 (Day 12)
**Status:** Accepted limitation; pivot Day 17 plan

### Context

While building silver_observations, ran sanity checks on HbA1c values for
T2DM patients and on blood pressure for hypertensive patients. Expected
clinically realistic distributions for each.

### Findings

**HbA1c:**
- 49% of all HbA1c readings (44,108 of 90,453) are below 4.0% — biologically
  impossible (incompatible with life). Population average before filtering:
  3.72%. After filtering implausible values: 5.9%.
- T2DM patients' average HbA1c after filtering: 5.6%. Lower than the
  population average. Real-world T2DM HbA1c averages 7-8% (uncontrolled to
  mildly controlled).

**Blood pressure:**
- Hypertensive patients: average systolic 116.7 mmHg across 68,012 readings.
- Non-hypertensive control group: average systolic 116.6 mmHg across 97,425
  readings.
- 0.1 mmHg difference. Real-world gap would be 15-25 mmHg.

### Root cause

Synthea assigns ICD/SNOMED diagnosis codes to patients but does not generate
correlated observation values. Diagnosed hypertensives are not given
elevated BP. Diagnosed diabetics are not given elevated HbA1c. This is a
known limitation of Synthea's underlying clinical generator — it models
encounter and diagnosis workflows but not pathophysiology.

### Decision

1. Keep all observation values in silver_observations. Do not silently
   filter. Add is_plausible_value boolean flag for downstream models to
   opt into clinically valid ranges.
2. Pivot Day 17 medication adherence narrative away from clinical-outcome
   metrics (HbA1c drop, BP drop) toward prescription-pattern metrics:
   - Treatment rate per condition (fill ratio)
   - Persistence (days between first and last prescription)
   - Coverage gaps (intervals between consecutive prescriptions of same drug)
   This aligns with industry-standard Proportion of Days Covered (PDC)
   methodology used in real medication adherence research where lab data
   is sparse or unreliable.
3. Document the limitation transparently in README and interview talking
   points. "I found and surfaced a synthetic data quality issue rather than
   hiding it" is a stronger story than fake clinical outcomes.

### Consequences

- Day 17 medication adherence becomes prescription-fill-pattern analysis,
  not lab-outcome analysis.
- Day 19 anomaly framework loses one possible anomaly type ("HbA1c spike
  in diabetic without medication change") but retains the other three
  (drug-drug interactions, early readmission, missed annual screening).
- Project's safety-first positioning is strengthened: the is_plausible_value
  flag and the transparent README disclosure demonstrate the data-quality
  rigor the project is supposed to model.
  ## DD-003: SNOMED suffix classification is a cross-resource pattern


### Context

DD-001 established that FHIR Condition resources need SNOMED suffix
classification (disorder / finding / situation) because Synthea encodes
diagnoses, social factors, and administrative events in the same table.

While building gold_readmissions, discovered the same pattern in
Encounter.reasonCode: Synthea uses history codes ("History of CABG"),
procedure codes ("Patient transfer to SNF"), and staging codes ("TNM
stage 1") as encounter reasons — inflating readmission cohorts by 3-4x
if used naively.

### Decision

Treat SNOMED suffix classification as a project-wide utility, not a
column specific to silver_conditions. Apply the same pattern to any
SNOMED-coded field: encounter reasons (done Day 15), observation
categories (Day 18 candidate), procedure classifications (Week 4 graph
ingestion candidate).

### Consequences

- gold_readmissions has three orthogonal filters (overlap, planned,
  clinical) that together bring the 30-day rate from 45% to 19% —
  close to real-world CMS 15%.
- Future Gold models must apply the clinical filter before publishing
  cohort counts. Add a documentation note to any new Gold model
  referencing SNOMED codes.
- Consider extracting the suffix classification into a dbt macro to
  avoid copy-paste across models. Deferred — do it if a fourth use
  case appears.


  ## DD-004: Synthea prescriptions are authorization events, not dispensing records

**Date:** 2026-XX-XX (Day 17)
**Status:** Accepted limitation. PDC ships as informational; persistence is
the operative adherence signal.

### Context

Built gold_medication_adherence using CMS-standard PDC (Proportion of Days
Covered) methodology over a 365-day window ending at last_prescription.
Expected class-level variation (insulin lower PDC than metformin, etc.) and
overall averages of 60-80% (consistent with published real-world PDC).

### Findings

PDC distribution is bimodal, not normally distributed:
- 64% of patient-drug pairs: PDC < 0.25 (sparse fills)
- 20%: PDC >= 0.80 (dense fills)
- 5% in the 0.50-0.80 middle range

Overall PDC averages by class: HTN 0.41, diabetes 0.50, HF 0.60, COPD 0.20.
All far below published real-world PDC benchmarks (~60-70% for chronic
oral maintenance).

Meanwhile, persistence (days from first to last prescription) is strong:
median HTN persistence 3,612 days (9.9 years). 91% of HTN pairs persist
>= 1 year. Diabetes and COPD similar. Heart failure lower (median 482 days)
as expected clinically.

### Root cause

Synthea generates MedicationRequest resources tied to encounters, not
between-encounter pharmacy refills. A real patient on daily metformin
generates ~12 pharmacy fill records per year even without physician visits.
A Synthea patient generates one MedicationRequest per physician visit —
which may be years apart even for chronic conditions.

This is not a Synthea bug. FHIR MedicationRequest is the authorization
event, not the dispensing event (that's MedicationDispense). Synthea
emits MedicationRequest correctly. It just doesn't simulate the
MedicationDispense stream that real pharmacies produce.

### Decision

1. Keep PDC in the model as an informational metric. Document that it
   reflects prescription density, not true medication coverage.
2. Add persistence_days as a peer metric and lead with it in analytics.
3. Adherence class buckets stay CMS-aligned (adherent >= 0.80) so the
   model matches industry standard, but downstream reporting must caveat
   that Synthea PDC underestimates real-world adherence by 30-50%.
4. Do NOT invent synthetic fill records to make PDC look better. Fudging
   generation data to match target metrics undermines the whole project.

### Consequences

- Any dashboard or interview claim using this table must lead with
  persistence and mention PDC with the DD-004 caveat.
- Week 4 knowledge graph should include MedicationRequest nodes with
  authored_on timestamps, not synthetic fill events.
- If a future MedicationDispense simulator is built (not this project),
  PDC would become the primary metric and persistence the secondary.
- Interview narrative: "I built industry-standard PDC and immediately
  found it doesn't work on Synthea data — because Synthea models
  prescriptions, not dispensing. Persistence is the right signal here.
  This is the kind of gap you only catch by validating your model
  against a known benchmark."
 ## DD-005: Anomaly types dropped after baseline analysis

**Date:** 2026-XX-XX (Day 19)
**Status:** Accepted

### Context

The Week 6 anomaly benchmark requires ground-truth injected anomalies with
measurable signal:noise ratios. Day 19 designed 4 anomaly types and
measured baseline detection counts (pre-injection) to size injected
volumes correctly.

### Findings

Baselines measured against the un-injected Silver tier:

| Anomaly | Baseline | Target | Ratio |
|---|---|---|---|
| Warfarin + NSAID/aspirin | 11 | 30 | 3:1 (ship) |
| HF 7-day readmission | 5 | 25 | 5:1 (ship) |
| Chronic-drug persistence gap (refined) | 2,433 | 40 | 1:60 (drop) |
| Post-discharge no-fill (refined) | 470 | 25 | 1:19 (drop) |

The dropped anomalies were refined once (still-engaged patient filter and
followup-encounter filter) before dropping. Refinement reduced baseline
counts by ~25% and 40% respectively but did not close the signal:noise gap.

### Root cause

Both dropped anomaly types measure ABSENCE of a subsequent prescription.
DD-004 documented that Synthea generates MedicationRequest per encounter,
not per pharmacy fill — so long gaps between prescriptions are the
statistical norm for chronic patients, not the exception. Baseline
detection queries return every natural gap as a hit.

### Decision

Ship 2 anomaly types (warfarin coprescription, HF early readmission).
Both measure event coincidence rather than event absence, which Synthea
models correctly.

### Consequences

- Benchmark suite has 2 injected types instead of 4. Statistically valid
  precision/recall for both.
- The AI agent (Day 32) is prompted to detect drug interactions and
  readmission anomalies. Adherence-style anomaly detection is out of scope.
- Interview narrative: "I designed 4 anomaly types, then dropped 2 after
  baselines showed Synthea's prescription-generation model made them
  undetectable. Better to measure 2 things well than 4 things poorly."
- If Bronze is ever rebuilt with MedicationDispense parsing (Week 4+
  extension), dropped anomalies become detectable and can be reinstated.
  
## DD-006: LLM choice — Ollama local over cloud APIs

**Date:** Day 29
**Status:** Accepted

### Context
Phase 3 GraphRAG agent needs an LLM for NL → Cypher generation and
answer synthesis. Three options evaluated: OpenAI GPT-4o (~$5-15 dev
cost, best Cypher quality), Claude API (similar), Ollama local (free,
offline, weaker Cypher).

### Decision
Ollama with qwen2.5-coder:7b. Same infrastructure philosophy as DuckDB
(Day 1) and Docker Neo4j (Day 22): free, no expiry, no API key in .env,
fully offline demo. Anyone cloning the repo can run the full stack with
zero accounts or credits.

### Tradeoff
7B model generates worse Cypher than GPT-4o on complex queries. Mitigated
by mandatory schema injection (transforms quality from unusable to correct
on tested patterns) and few-shot examples for complex query types.
If Cypher accuracy proves insufficient for the demo, can switch to
OpenAI as a fallback profile without architectural changes — LangChain
abstracts the LLM provider.

### Consequences
- Every agent prompt must include the full graph schema
- 8-12 few-shot Cypher examples required (vs ~4 for GPT-4o)
- Demo runs fully offline on any machine with 14GB+ RAM
- No API cost, no rate limits, no key rotation

## DD-007: Template-based patient summaries over LLM-generated

**Date:** Day 26
**Status:** Accepted

### Context
Chroma needs text documents to embed. Two approaches: template-based
(Python script generates structured summaries from Silver data) or
LLM-generated (feed structured data to LLM, get narrative summaries).

### Decision
Template-based. Deterministic, free, reproducible. Summary generation
runs in 0.5 seconds for 11,446 patients with zero API calls.

### Consequences
- Summaries are structured and consistent but not narrative-quality
- Semantic search works well for demographic + condition matching
  (distances 0.40-0.48 on test queries)
- Weaker on abstract clinical concepts ("worsening symptoms") because
  templates don't capture clinical trajectory
- If narrative quality matters later, can re-generate with LLM as a
  one-time batch job without changing the embedding pipeline