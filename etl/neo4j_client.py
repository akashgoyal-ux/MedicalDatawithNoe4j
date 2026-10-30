"""Shared Neo4j connection helper for ETL, API, and app layers."""
from contextlib import contextmanager

from neo4j import GraphDatabase

from etl.config import neo4j_settings

_driver = None


def get_driver():
    global _driver
    cfg = neo4j_settings()
    if _driver is None:
        _driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
        _driver.verify_connectivity()
    return _driver


@contextmanager
def session():
    with get_driver().session(database=neo4j_settings()["database"]) as s:
        yield s


def run(query: str, **params):
    with session() as s:
        return [dict(r) for r in s.run(query, **params)]


def run_write(query: str, **params):
    with session() as s:
        return s.execute_write(lambda tx: tx.run(query, **params).consume())
