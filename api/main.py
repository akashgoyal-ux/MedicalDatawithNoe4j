"""FastAPI service wrapping the graph-assisted search expansion from the talk.

Run: uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from etl.neo4j_client import run

app = FastAPI(
    title="Medical Data Neo4j — prototype",
    description="Graph-assisted regulatory search expansion (ATC + MeSH), per implementation-plan.md",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in
                   os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/safety/{drug_name}")
def drug_safety(drug_name: str, limit: int = Query(15, ge=1, le=100)):
    """FAERS profile for a drug: report tallies + top reactions (MedDRA PTs)."""
    summary = run(
        """
        MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
        RETURN count(r) AS reports,
               sum(CASE WHEN r.serious THEN 1 ELSE 0 END) AS serious,
               sum(CASE WHEN r.death THEN 1 ELSE 0 END) AS deaths,
               sum(CASE WHEN r.hospitalization THEN 1 ELSE 0 END) AS hospitalizations
        """,
        drug=drug_name,
    )[0]
    if summary["reports"] == 0:
        raise HTTPException(404, f"no FAERS reports found for {drug_name!r}")
    reactions = run(
        """
        MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
              -[:REPORTS_REACTION]->(x:Reaction)
        OPTIONAL MATCH (x)-[:MAPS_TO_MESH]->(m:MeshDescriptor)
        RETURN x.name AS reaction, count(r) AS reports,
               sum(CASE WHEN r.death THEN 1 ELSE 0 END) AS deaths,
               m.name AS meshDescriptor
        ORDER BY reports DESC, deaths DESC LIMIT $limit
        """,
        drug=drug_name, limit=limit,
    )
    return {"drug": drug_name, "summary": summary, "topReactions": reactions}


@app.get("/timeline")
def approval_timeline(term: str = Query(..., min_length=2),
                      depth: int = Query(13, ge=1, le=13)):
    """Approvals per year for a disease (graph-expanded) — temporal strategy view."""
    result = expand_term(term, depth)
    if result is None:
        raise HTTPException(404, f"no MeSH descriptor matched {term!r}")
    rows = run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) IN [t IN $terms | toLower(t)] AND d.date IS NOT NULL
        RETURN substring(d.date, 0, 4) AS year, count(DISTINCT d) AS approvals
        ORDER BY year
        """,
        terms=result["terms"],
    )
    return {"term": term, "matched_descriptor": result["descriptor"], "timeline": rows}


@app.get("/trials")
def trials_for_disease(term: str = Query(..., min_length=2),
                       depth: int = Query(5, ge=1, le=13),
                       limit: int = Query(50, ge=1, le=200)):
    """Pipeline view: trials studying a disease or its descendants."""
    result = expand_term(term, depth)
    if result is None:
        raise HTTPException(404, f"no MeSH descriptor matched {term!r}")
    rows = run(
        """
        MATCH (t:Trial)-[:STUDIES]->(m:MeshDescriptor)
        WHERE toLower(m.name) IN [x IN $terms | toLower(x)]
        OPTIONAL MATCH (t)-[:SPONSORED_BY]->(s:Sponsor)
        OPTIONAL MATCH (t)-[:TESTS]->(d:Drug)
        RETURN t.nctId AS nctId, t.title AS title, t.phase AS phase,
               t.status AS status, t.startDate AS startDate,
               s.name AS sponsor, collect(DISTINCT d.name) AS drugsTested
        ORDER BY t.startDate DESC LIMIT $limit
        """,
        terms=result["terms"], limit=limit,
    )
    return {"term": term, "matched_descriptor": result["descriptor"],
            "count": len(rows), "trials": rows}


@app.get("/similar/{drug_name}")
def similar_drugs(drug_name: str, limit: int = Query(10, ge=1, le=50)):
    """Repurposing candidates: drugs sharing indications / reaction profiles."""
    rows = run(
        """
        MATCH (d:Drug {name: $drug})-[e:SIMILAR_TO]-(other:Drug)
        RETURN other.name AS drug, e.sharedIndications AS sharedIndications,
               e.sharedReactions AS sharedReactions, e.score AS score
        ORDER BY score DESC LIMIT $limit
        """,
        drug=drug_name, limit=limit,
    )
    if not rows:
        raise HTTPException(404, f"no SIMILAR_TO edges for {drug_name!r} — "
                                 "run python -m etl.compute_similarity first")
    return {"drug": drug_name, "similar": rows}


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    """GraphRAG chatbot (LangGraph + local Ollama LLM) over the knowledge graph."""
    from chat.agent import chat
    try:
        return chat(req.message)
    except Exception as e:  # LLM offline etc.
        raise HTTPException(503, f"chat unavailable: {e}")


@app.get("/vsearch")
def vector_search(q: str = Query(..., min_length=2), k: int = Query(5, ge=1, le=20)):
    """Semantic vector search over docs, drugs, reactions, active MeSH terms."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    r = requests.post(f"{base}/api/embed", json={"model": model, "input": [q]}, timeout=120)
    r.raise_for_status()
    vec = r.json()["embeddings"][0]
    hits = []
    for index, kind in (("docEmbedding", "document"), ("meshEmbedding", "meshTerm"),
                        ("reactionEmbedding", "reaction"), ("drugEmbedding", "drug")):
        for row in run(
            f"CALL db.index.vector.queryNodes('{index}', $k, $vec) YIELD node, score "
            "RETURN coalesce(node.title, node.name) AS text, score",
            k=k, vec=vec,
        ):
            hits.append({"kind": kind, "text": row["text"], "score": row["score"]})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {"query": q, "hits": hits[: k * 2]}


@app.get("/drugs")
def list_drugs(with_reports: bool = False):
    """Drug names in the graph; optionally only those with FAERS reports."""
    if with_reports:
        return [r["n"] for r in run(
            "MATCH (d:Drug)-[:HAS_REPORT]->(:AdverseEventReport) "
            "RETURN DISTINCT d.name AS n ORDER BY n")]
    return [r["n"] for r in run("MATCH (d:Drug) RETURN d.name AS n ORDER BY n")]


@app.get("/stats")
def graph_stats():
    """Live graph statistics, enumerated from the ontology registry —
    every declared node class and edge type is counted."""
    from etl.ontology import SCHEMA
    nodes = {name: run(f"MATCH (n:{name}) RETURN count(n) AS c")[0]["c"]
             for name in SCHEMA.nodes}
    edges = {name: run(f"MATCH ()-[r:{name}]->() RETURN count(r) AS c")[0]["c"]
             for name in SCHEMA.edges}
    embedded = run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c")[0]["c"]
    vector_indexes = [r["name"] for r in run(
        "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name")]
    return {
        "nodes": nodes,
        "edges": edges,
        "embeddedNodes": embedded,
        "vectorIndexes": vector_indexes,
        "chatModel": os.getenv("OLLAMA_CHAT_MODEL", "qwen3.6:27b-mlx"),
        "embedModel": os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        "ontology": {"classes": len(SCHEMA.nodes), "edgeTypes": len(SCHEMA.edges)},
    }


@app.get("/health")
def health():
    return run("RETURN 1 AS ok")[0]
