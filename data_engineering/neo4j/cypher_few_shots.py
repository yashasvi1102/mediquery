"""
Day 31: Few-shot Cypher examples for the GraphRAG agent.

These examples target the specific failure patterns found on Day 30:
  - Multi-hop paths (Patient → Encounter → Medication)
  - CONTAINS for medication/condition names
  - Counting encounters vs patients
  - Temporal queries (readmissions)
  - Condition queries using display vs condition_flag

Import in graphrag_agent.py and inject into the prompt.
"""

FEW_SHOT_EXAMPLES = [
    # 1. Multi-hop medication query (Day 30 failure: used Patient-[:PRESCRIBED] directly)
    {
        "question": "Which patients are prescribed warfarin?",
        "cypher": """MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter)-[:PRESCRIBED]->(m:Medication)
WHERE m.medication_display CONTAINS 'Warfarin'
RETURN DISTINCT p.patient_id, p.given_name, p.family_name
LIMIT 25"""
    },

    # 2. Co-prescription / drug interaction (Day 30 failure: wrong path + exact match)
    {
        "question": "Find patients prescribed both warfarin and aspirin",
        "cypher": """MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter)-[:PRESCRIBED]->(m1:Medication)
WHERE m1.medication_display CONTAINS 'Warfarin'
WITH DISTINCT p
MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter)-[:PRESCRIBED]->(m2:Medication)
WHERE m2.medication_display CONTAINS 'Aspirin'
RETURN DISTINCT p.patient_id, p.given_name, p.family_name
LIMIT 25"""
    },

    # 3. Count encounters by type (Day 30 partial: counted patients instead of encounters)
    {
        "question": "What is the most common encounter type?",
        "cypher": """MATCH (e:Encounter)
RETURN e.encounter_type AS encounter_type, count(e) AS encounter_count
ORDER BY encounter_count DESC"""
    },

    # 4. Most common condition by display name (Day 30 failure: used condition_flag which is mostly NULL)
    {
        "question": "Which condition has the most patients?",
        "cypher": """MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition)
WHERE c.clinical_category = 'disorder'
RETURN c.display AS condition, count(DISTINCT p) AS patient_count
ORDER BY patient_count DESC
LIMIT 10"""
    },

    # 5. Chronic condition cohort with age filter
    {
        "question": "How many diabetic patients are over 65?",
        "cypher": """MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {condition_flag: 'diabetes_t2'})
WHERE p.age_years_current > 65
RETURN count(DISTINCT p) AS patient_count"""
    },

    # 6. Readmission within time window (temporal query)
    {
        "question": "Find patients readmitted within 30 days",
        "cypher": """MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e1:Encounter {is_inpatient: true})
MATCH (p)-[:HAS_ENCOUNTER]->(e2:Encounter {is_inpatient: true})
WHERE e2.start_time > e1.end_time
  AND e2.start_time <= e1.end_time + duration({days: 30})
  AND e1.encounter_id <> e2.encounter_id
RETURN DISTINCT p.patient_id, p.given_name, e1.encounter_id AS index_encounter,
       e2.encounter_id AS readmission_encounter,
       duration.between(e1.end_time, e2.start_time).days AS days_between
ORDER BY days_between
LIMIT 25"""
    },

    # 7. Medications for a condition cohort (multi-hop both directions)
    {
        "question": "What medications are prescribed to heart failure patients?",
        "cypher": """MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {condition_flag: 'heart_failure'})
WITH DISTINCT p
MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)-[:PRESCRIBED]->(m:Medication)
RETURN m.medication_display AS medication, m.drug_class AS drug_class,
       count(DISTINCT p) AS patient_count
ORDER BY patient_count DESC
LIMIT 15"""
    },

    # 8. Provider volume
    {
        "question": "Which provider has the most encounters?",
        "cypher": """MATCH (e:Encounter)-[:TREATED_BY]->(prov:Provider)
RETURN prov.provider_id, count(e) AS encounter_count
ORDER BY encounter_count DESC
LIMIT 10"""
    },

    # 9. Patients with multiple chronic conditions (comorbidity)
    {
        "question": "Find patients with both diabetes and hypertension",
        "cypher": """MATCH (p:Patient)-[:HAS_CONDITION]->(c1:Condition {condition_flag: 'diabetes_t2'})
MATCH (p)-[:HAS_CONDITION]->(c2:Condition {condition_flag: 'hypertension'})
RETURN DISTINCT p.patient_id, p.given_name, p.family_name,
       p.age_years_current, p.gender
LIMIT 25"""
    },

    # 10. Gender/demographic breakdown of a cohort
    {
        "question": "What is the gender breakdown of COPD patients?",
        "cypher": """MATCH (p:Patient)-[:HAS_CONDITION]->(c:Condition {condition_flag: 'copd'})
RETURN p.gender AS gender, count(DISTINCT p) AS patient_count
ORDER BY patient_count DESC"""
    },
]


def format_few_shots() -> str:
    """Format examples as a string for prompt injection."""
    parts = []
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, 1):
        parts.append(f"Example {i}:")
        parts.append(f"Question: {ex['question']}")
        parts.append(f"Cypher: {ex['cypher']}")
        parts.append("")
    return "\n".join(parts)


if __name__ == "__main__":
    print(format_few_shots())
    print(f"\n{len(FEW_SHOT_EXAMPLES)} few-shot examples ready.")