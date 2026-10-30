"""FastAPI service wrapping the graph-assisted search expansion from the talk.

Run: uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from etl.neo4j_client import run

app = FastAPI(
    title="Medical Data Neo4j — prototype",
    description="Graph-assisted regulatory search expansion (ATC + MeSH), per implementation-plan.md",
    version="0.1.0",
)


class ExpansionResult(BaseModel):
    term: str
    matched_descriptor: str | None
    descriptor_ui: str | None
    expanded_terms: list[str]
    count: int


class DocumentHit(BaseModel):
    docId: str
    title: str
    agency: str
    date: str | None
    url: str


class SearchResult(BaseModel):
    term: str
    expansion: ExpansionResult
    documents: list[DocumentHit]
    baseline_count: int
    expanded_count: int


def expand_term(term: str, depth: int = 13) -> dict | None:
    """Query 6.2 from the plan: fulltext lookup -> descendants + synonyms."""
    rows = run(
        """
        CALL db.index.fulltext.queryNodes('meshNames', $term) YIELD node, score
        WITH node ORDER BY score DESC LIMIT 1
        OPTIONAL MATCH (node)-[:SYNONYM_OF]->(d:MeshDescriptor)
        WITH coalesce(d, node) AS start
        MATCH (start:MeshDescriptor)
        OPTIONAL MATCH (start)<-[:SYNONYM_OF]-(syn:MeshTerm)
        WITH start, collect(DISTINCT start.name) + collect(DISTINCT syn.name) AS base
        OPTIONAL MATCH path = (start)-[:BROADER_THAN*1..13]->(child:MeshDescriptor)
        WHERE length(path) <= $depth
        OPTIONAL MATCH (child)<-[:SYNONYM_OF]-(csyn:MeshTerm)
        WITH start, base,
             collect(DISTINCT child.name) AS childNames,
             collect(DISTINCT csyn.name) AS childSyns
        RETURN start.name AS descriptor, start.ui AS ui,
               base + childNames + childSyns AS terms
        """,
        term=term, depth=depth,
    )
    if not rows or rows[0]["descriptor"] is None:
        return None
    terms = sorted({t for t in rows[0]["terms"] if t})
    return {"descriptor": rows[0]["descriptor"], "ui": rows[0]["ui"], "terms": terms}


@app.get("/expand", response_model=ExpansionResult)
def expand(term: str = Query(..., min_length=2),
           depth: int = Query(13, ge=1, le=13)):
    result = expand_term(term, depth)
    if result is None:
        raise HTTPException(404, f"no MeSH descriptor matched {term!r}")
    return ExpansionResult(
        term=term,
        matched_descriptor=result["descriptor"],
        descriptor_ui=result["ui"],
        expanded_terms=result["terms"],
        count=len(result["terms"]),
    )


@app.get("/atc/{code}")
def decode_atc(code: str):
    """Query 6.1: decode an ATC code into its full 5-level 'biography'."""
    rows = run(
        """
        MATCH path = (l1:ATCLevel1)-[:HAS_SUBGROUP|HAS_SUBSTANCE*..4]->(l5:ATCLevel5 {code: $code})
        RETURN [n IN nodes(path) | {level: n.level, code: n.code, name: n.name}] AS breakdown
        """,
        code=code.upper(),
    )
    if not rows:
        raise HTTPException(404, f"ATC code {code!r} not found")
    return {"code": code.upper(), "breakdown": rows[0]["breakdown"]}


@app.get("/search", response_model=SearchResult)
def search(term: str = Query(..., min_length=2),
           depth: int = Query(13, ge=1, le=13),
           limit: int = Query(100, ge=1, le=500)):
    """Queries 6.2 + 6.3: expand the term, then find documents for all terms.
    Also returns exact-match baseline count to demonstrate the recall lift."""
    result = expand_term(term, depth)
    if result is None:
        raise HTTPException(404, f"no MeSH descriptor matched {term!r}")

    docs = run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) IN [t IN $terms | toLower(t)]
        MATCH (d)-[:ISSUED_BY]->(a:Agency)
        RETURN DISTINCT d.docId AS docId, d.title AS title, a.name AS agency,
               d.date AS date, d.url AS url
        ORDER BY d.date DESC LIMIT $limit
        """,
        terms=result["terms"], limit=limit,
    )
    baseline = run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) = toLower($term)
        RETURN count(DISTINCT d) AS c
        """,
        term=term,
    )[0]["c"]
    return SearchResult(
        term=term,
        expansion=ExpansionResult(
            term=term, matched_descriptor=result["descriptor"],
            descriptor_ui=result["ui"], expanded_terms=result["terms"],
            count=len(result["terms"]),
        ),
        documents=[DocumentHit(**d) for d in docs],
        baseline_count=baseline,
        expanded_count=len(docs),
    )


@app.get("/health")
def health():
    return run("RETURN 1 AS ok")[0]
