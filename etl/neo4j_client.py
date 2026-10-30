"""Shared Neo4j connection helper for ETL, API, and app layers."""
from contextlib import contextmanager

from neo4j import GraphDatabase

from etl.config import NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _driver.verify_connectivity()
    return _driver


@contextmanager
def session():
    with get_driver().session(database=NEO4J_DATABASE) as s:
        yield s


def run(query: str, **params):
    with session() as s:
        return [dict(r) for r in s.run(query, **params)]


def run_write(query: str, **params):
    with session() as s:
        return s.execute_write(lambda tx: tx.run(query, **params).consume())
