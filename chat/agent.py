"""GraphRAG chatbot: LangGraph ReAct agent over the medical Neo4j graph.

Runs fully local: ChatOllama (llama3.2) as the LLM, tools call the same
Cypher queries as the REST API — MeSH expansion, document search, ATC decode,
FAERS safety, trials pipeline, similarity, and vector semantic search.

Used by api/main.py /chat and can be run standalone:
    python -m chat.agent "Which drugs are indicated for diabetes and what reactions do they have?"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

import requests
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from etl.neo4j_client import run

OLLAMA_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:3b")
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


@tool
def mesh_expand(term: str) -> str:
    """Expand a disease/medical term to all MeSH synonyms and descendant terms.
    Use first for any disease question to discover related terminology."""
    from api.main import expand_term
    result = expand_term(term)
    if result is None:
        return f"No MeSH descriptor matched {term!r}."
    return (f"Matched descriptor: {result['descriptor']} (ui {result['ui']}). "
            f"{len(result['terms'])} expanded terms incl.: "
            + ", ".join(result["terms"][:15]))


@tool
def search_documents(term: str) -> str:
    """Find regulatory documents (FDA labels) mentioning a disease term,
    using graph-expanded terminology."""
    from api.main import expand_term
    result = expand_term(term)
    if result is None:
        return f"No MeSH descriptor matched {term!r}."
    docs = run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) IN [t IN $terms | toLower(t)]
        RETURN DISTINCT d.title AS title, d.date AS date, d.url AS url
        ORDER BY d.date DESC LIMIT 10
        """,
        terms=result["terms"],
    )
    if not docs:
        return "No documents found."
    return "\n".join(f"- {d['title']} ({d['date']}) {d['url']}" for d in docs)


@tool
def drug_safety(drug_name: str) -> str:
    """FAERS adverse-event profile of a drug: report counts, deaths,
    hospitalizations, top reactions."""
    summary = run(
        """
        MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
        RETURN count(r) AS reports,
               sum(CASE WHEN r.serious THEN 1 ELSE 0 END) AS serious,
               sum(CASE WHEN r.death THEN 1 ELSE 0 END) AS deaths,
               sum(CASE WHEN r.hospitalization THEN 1 ELSE 0 END) AS hosp
        """,
        drug=drug_name.title(),
    )[0]
    if not summary["reports"]:
        return f"No FAERS reports for {drug_name!r} (try the generic name, e.g. 'Ibuprofen')."
    reactions = run(
        """
        MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
              -[:REPORTS_REACTION]->(x:Reaction)
        RETURN x.name AS reaction, count(r) AS n
        ORDER BY n DESC LIMIT 8
        """,
        drug=drug_name.title(),
    )
    return (f"{drug_name}: {summary['reports']} reports, {summary['serious']} serious, "
            f"{summary['deaths']} deaths, {summary['hosp']} hospitalizations. "
            "Top reactions: " + ", ".join(f"{r['reaction']} ({r['n']})" for r in reactions))


@tool
def trials_pipeline(disease: str) -> str:
    """Clinical trial pipeline for a disease: phases, statuses, sponsors."""
    rows = run(
        """
        CALL db.index.fulltext.queryNodes('meshNames', $term) YIELD node, score
        WITH node ORDER BY score DESC LIMIT 1
        OPTIONAL MATCH (node)-[:SYNONYM_OF]->(d0:MeshDescriptor)
        WITH coalesce(d0, node) AS d
        MATCH (t:Trial)-[:STUDIES]->(d)
        OPTIONAL MATCH (t)-[:SPONSORED_BY]->(s:Sponsor)
        OPTIONAL MATCH (t)-[:TESTS]->(dr:Drug)
        RETURN t.nctId AS nct, t.title AS title, t.phase AS phase,
               t.status AS status, s.name AS sponsor,
               collect(DISTINCT dr.name) AS drugs
        ORDER BY t.startDate DESC LIMIT 10
        """,
        term=disease,
    )
    if not rows:
        return f"No trials found for {disease!r}."
    return "\n".join(
        f"- [{r['nct']}] ({r['phase'] or 'phase n/a'}, {r['status']}) {r['title']} "
        f"— sponsor: {r['sponsor'] or 'n/a'}"
        + (f" — tests: {', '.join(r['drugs'])}" if r['drugs'] else "")
        for r in rows
    )


@tool
def decode_atc(code: str) -> str:
    """Decode an ATC code (e.g. C03XA01) into its 5-level classification."""
    rows = run(
        """
        MATCH path = (l1:ATCLevel1)-[:HAS_SUBGROUP|HAS_SUBSTANCE*..4]->(l5:ATCLevel5 {code: $code})
        RETURN [n IN nodes(path) | n.code + ' — ' + n.name] AS breakdown
        """,
        code=code.upper(),
    )
    if not rows:
        return f"ATC code {code!r} not found."
    return " → ".join(rows[0]["breakdown"])


@tool
def similar_drugs(drug_name: str) -> str:
    """Drugs similar to the given one (shared indications and/or shared
    adverse-reaction profiles) — repurposing candidates."""
    rows = run(
        """
        MATCH (d:Drug {name: $drug})-[e:SIMILAR_TO]-(o:Drug)
        RETURN o.name AS drug, e.sharedIndications AS ind, e.score AS score
        ORDER BY score DESC LIMIT 8
        """,
        drug=drug_name.title(),
    )
    if not rows:
        return f"No similarity data for {drug_name!r}."
    return ", ".join(f"{r['drug']} (shared indications: {r['ind']})" for r in rows)


@tool
def semantic_search(query: str) -> str:
    """Semantic (vector) search over drug names, document titles, reactions,
    and active MeSH descriptors — use for fuzzy/conceptual questions."""
    r = requests.post(f"{OLLAMA_BASE}/api/embed",
                      json={"model": EMBED_MODEL, "input": [query]}, timeout=120)
    r.raise_for_status()
    vec = r.json()["embeddings"][0]
    hits = []
    for index, kind in (("docEmbedding", "document"), ("meshEmbedding", "disease term"),
                        ("reactionEmbedding", "reaction"), ("drugEmbedding", "drug")):
        rows = run(
            f"""
            CALL db.index.vector.queryNodes('{index}', 3, $vec) YIELD node, score
            RETURN coalesce(node.title, node.name) AS text, score
            """,
            vec=vec,
        )
        hits.extend(f"- [{kind}] {r['text']} (score {r['score']:.3f})" for r in rows)
    return "Top semantic matches:\n" + "\n".join(hits[:10]) if hits else "No vector index data."


TOOLS = [mesh_expand, search_documents, drug_safety, trials_pipeline,
         decode_atc, similar_drugs, semantic_search]

SYSTEM_PROMPT = (
    "You are Ria, a regulatory intelligence assistant for the pharmaceutical domain. "
    "You answer questions about drugs, diseases, regulatory documents, clinical trials, "
    "and drug safety using the connected Neo4j knowledge graph via your tools. "
    "Always ground answers in tool results — never invent drug, trial, or safety facts. "
    "Be concise and business-friendly: lead with the answer, then supporting detail. "
    "When listing documents or trials, mention counts and the top few items."
)

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE, temperature=0)
        _agent = create_react_agent(llm, TOOLS, prompt=SYSTEM_PROMPT)
    return _agent


def chat(message: str) -> dict:
    """Run one chat turn; returns answer + tool call trace."""
    agent = get_agent()
    result = agent.invoke({"messages": [("user", message)]})
    tool_calls = [
        {"tool": tc["name"], "args": tc["args"]}
        for m in result["messages"] if getattr(m, "tool_calls", None)
        for tc in m.tool_calls
    ]
    return {"answer": result["messages"][-1].content, "toolCalls": tool_calls}


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What reactions are reported for ibuprofen?"
    print(chat(question)["answer"])
