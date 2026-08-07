"""
Neo4j connection wrapper for MediQuery.

Same design philosophy as connection.py for DuckDB: centralize the
connection config so no script hardcodes URIs or credentials.

Usage:
    from data_engineering.neo4j.neo4j_connection import get_neo4j_driver, get_neo4j_session

    driver = get_neo4j_driver()
    with get_neo4j_session() as session:
        result = session.run("MATCH (n) RETURN count(n) AS count")
        print(result.single()["count"])
    driver.close()

Environment variables (optional overrides):
    NEO4J_URI       — default: bolt://localhost:7687
    NEO4J_USER      — default: neo4j
    NEO4J_PASSWORD  — default: mediquery2026
"""

import os
from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "mediquery2026")

_driver = None


def get_neo4j_driver():
    """Return a singleton Neo4j driver instance."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def get_neo4j_session(**kwargs):
    """Return a new Neo4j session. Caller is responsible for closing it."""
    return get_neo4j_driver().session(**kwargs)


def close_driver():
    """Explicitly close the driver (call at script exit)."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connection():
    """Quick connectivity check. Returns True if Neo4j is reachable."""
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        return True
    except Exception as e:
        print(f"Neo4j connection failed: {e}")
        return False
