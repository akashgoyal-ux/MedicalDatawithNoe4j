"""Streamlit demo UI for the graph-assisted regulatory search prototype.

Run: streamlit run app/streamlit_app.py
Tabs:
  1. Search — type a disease term, see MeSH expansion + matching documents
  2. ATC decoder — type a code like C03XA01, see the 5-level breakdown
"""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from api.main import expand_term
from etl.config import neo4j_browser_url, neo4j_settings
from etl.neo4j_client import run

st.set_page_config(page_title="MedGraph — Regulatory Intelligence", layout="wide")
st.title("🧬 MedGraph")
st.caption("Graph-assisted search expansion with Neo4j — MeSH + WHO ATC "
           "(replica of the 'Enhancing Medical Data Processing with Neo4j' talk)")


@st.cache_data(ttl=300)
def cached_expand(term: str, depth: int):
    return expand_term(term, depth)


def find_documents(terms: list[str], limit: int = 100) -> list[dict]:
    return run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) IN [t IN $terms | toLower(t)]
        MATCH (d)-[:ISSUED_BY]->(a:Agency)
        RETURN DISTINCT d.docId AS docId, d.title AS Title, a.name AS Agency,
               d.date AS Date, d.url AS URL
        ORDER BY d.date DESC LIMIT $limit
        """,
        terms=terms, limit=limit,
    )


def baseline_count(term: str) -> int:
    return run(
        """
        MATCH (d:RegulatoryDocument)-[:MENTIONS]->(m:MeshDescriptor)
        WHERE toLower(m.name) = toLower($term)
        RETURN count(DISTINCT d) AS c
        """,
        term=term,
    )[0]["c"]


tab_search, tab_atc, tab_graph, tab_safety = st.tabs(
    ["🔍 MeSH-expanded search", "💊 ATC code decoder", "🕸️ Graph neighborhood",
     "⚠️ Drug safety (FAERS)"]
)

with tab_search:
    col_input, col_depth = st.columns([4, 1])
    term = col_input.text_input("Disease / indication search term",
                                value="Diabetes Mellitus")
    depth = col_depth.selectbox("Expansion depth", [13, 5, 3], index=0)

    if term:
        with st.spinner("Traversing MeSH graph…"):
            result = cached_expand(term, depth)
        if result is None:
            st.warning(f"No MeSH descriptor matched **{term}**.")
        else:
            st.success(f"Matched **{result['descriptor']}** (`{result['ui']}`) — "
                       f"expanded to **{len(result['terms'])}** terms "
                       f"(descriptor + synonyms + descendants up to depth {depth})")

            left, right = st.columns([1, 2])
            with left:
                st.subheader("Expanded terms")
                st.dataframe(pd.DataFrame(result["terms"], columns=["term"]),
                             height=500, hide_index=True)
            with right:
                docs = find_documents(result["terms"])
                base = baseline_count(term)
                st.subheader("Regulatory documents")
                m1, m2 = st.columns(2)
                m1.metric("Exact-match recall (baseline)", base)
                m2.metric("Graph-expanded recall", len(docs),
                          delta=f"+{len(docs) - base}" if len(docs) > base else None)
                if docs:
                    st.dataframe(pd.DataFrame(docs), hide_index=True,
                                 column_config={"URL": st.column_config.LinkColumn()})
                else:
                    st.info("No documents matched yet — run `python -m etl.ingest_openfda` first.")

with tab_atc:
    code = st.text_input("ATC code", value="C03XA01").upper().strip()
    if code:
        rows = run(
            """
            MATCH path = (l1:ATCLevel1)-[:HAS_SUBGROUP|HAS_SUBSTANCE*..4]->(l5:ATCLevel5 {code: $code})
            RETURN [n IN nodes(path) | {level: n.level, code: n.code, name: n.name}] AS breakdown
            """,
            code=code,
        )
        if not rows:
            st.warning(f"ATC code **{code}** not found — load its branch first "
                       f"(`python -m etl.load_atc --groups {code[:1]}`).")
        else:
            st.subheader(f"'Biography' of {code}")
            for step in rows[0]["breakdown"]:
                indent = "&nbsp;" * 8 * (step["level"] - 1)
                st.markdown(f"{indent}**L{step['level']}** `{step['code']}` — {step['name']}",
                            unsafe_allow_html=True)
            drugs = run(
                """
                MATCH (l5:ATCLevel5 {code: $code})-[:CLASSIFIES]->(d:Drug)
                OPTIONAL MATCH (d)-[:INDICATED_FOR]->(m:MeshDescriptor)
                RETURN d.name AS Drug, collect(DISTINCT m.name)[..5] AS Indications
                """,
                code=code,
            )
            if drugs:
                st.subheader("Drugs at this code")
                st.dataframe(pd.DataFrame(drugs), hide_index=True)

# --- Disease traversal queries (graph form for Neo4j Browser + flat preview) ---
TRAVERSAL_QUERIES = [
    {
        "title": "Ego network — everything connected to the disease",
        "description": "Descendants (2 levels), synonyms, drugs, ATC chains, documents, and "
                       "agency — one UNION per path family so nothing is lost to cross-product "
                       "truncation. Note: some descriptors (e.g. Diabetes Mellitus) have no "
                       "synonym terms in the 2026 MeSH XML.",
        "graph": """
MATCH (d:MeshDescriptor {name: '$NAME'})
CALL (d) {
  WITH d
  MATCH p = (d)-[:BROADER_THAN*1..2]->(child:MeshDescriptor) RETURN p
  UNION ALL
  WITH d
  MATCH p = (d)<-[:SYNONYM_OF]-(syn:MeshTerm) RETURN p
  UNION ALL
  WITH d
  MATCH p = (drug:Drug)-[:INDICATED_FOR]->(d) RETURN p
  UNION ALL
  WITH d
  MATCH p = (l1:ATCLevel1)-[:HAS_SUBGROUP|HAS_SUBSTANCE*0..4]->(l5:ATCLevel5)<-[:HAS_ATC_CODE]-(drug:Drug)-[:INDICATED_FOR]->(d) RETURN p
  UNION ALL
  WITH d
  MATCH p = (a:Agency)<-[:ISSUED_BY]-(doc:RegulatoryDocument)-[:MENTIONS]->(d) RETURN p
}
RETURN p LIMIT 1000
""".strip(),
        "preview": """
MATCH (d:MeshDescriptor {name: $name})
OPTIONAL MATCH (d)-[:BROADER_THAN*1..2]->(child:MeshDescriptor)
OPTIONAL MATCH (d)<-[:SYNONYM_OF]-(syn:MeshTerm)
OPTIONAL MATCH (drug:Drug)-[:INDICATED_FOR]->(d)
OPTIONAL MATCH (drug)-[:HAS_ATC_CODE]->(l5:ATCLevel5)
OPTIONAL MATCH (doc:RegulatoryDocument)-[:MENTIONS]->(d)
OPTIONAL MATCH (doc)-[:ISSUED_BY]->(a:Agency)
RETURN count(DISTINCT child) AS `Descendants (2 levels)`,
       count(DISTINCT syn) AS Synonyms,
       count(DISTINCT drug) AS Drugs,
       count(DISTINCT l5) AS `Drugs w/ ATC code`,
       count(DISTINCT doc) AS Documents,
       count(DISTINCT a) AS Agencies
""".strip(),
    },
    {
        "title": "Full descendant subtree (up to 13 levels)",
        "description": "Every MeSH descriptor below the disease — the expansion set used by search.",
        "graph": """
MATCH path = (d:MeshDescriptor {name: '$NAME'})-[:BROADER_THAN*1..13]->(child)
RETURN path
""".strip(),
        "preview": """
MATCH (d:MeshDescriptor {name: $name})-[:BROADER_THAN*1..13]->(child:MeshDescriptor)
RETURN DISTINCT child.name AS Descendant, child.ui AS UI
ORDER BY Descendant LIMIT 200
""".strip(),
    },
    {
        "title": "Ancestors — both parents and the paths to the roots",
        "description": "MeSH is a DAG: e.g. Diabetes Mellitus sits under Endocrine AND Metabolic diseases.",
        "graph": """
MATCH path = (ancestor:MeshDescriptor)-[:BROADER_THAN*1..13]->(d:MeshDescriptor {name: '$NAME'})
RETURN path
""".strip(),
        "preview": """
MATCH (ancestor:MeshDescriptor)-[:BROADER_THAN*1..13]->(d:MeshDescriptor {name: $name})
RETURN DISTINCT ancestor.name AS Ancestor, ancestor.ui AS UI
ORDER BY Ancestor LIMIT 200
""".strip(),
    },
    {
        "title": "Disease → drugs → ATC biography",
        "description": "Crosses both vocabularies: indicated drugs and their 5-level ATC classification.",
        "graph": """
MATCH (d:MeshDescriptor {name: '$NAME'})<-[:INDICATED_FOR]-(drug:Drug)
OPTIONAL MATCH atcPath = (:ATCLevel1)-[:HAS_SUBGROUP|HAS_SUBSTANCE*..4]->(:ATCLevel5)-[:CLASSIFIES]->(drug)
RETURN d, drug, atcPath
""".strip(),
        "preview": """
MATCH (d:MeshDescriptor {name: $name})<-[:INDICATED_FOR]-(drug:Drug)
OPTIONAL MATCH (l5:ATCLevel5)-[:CLASSIFIES]->(drug)
RETURN drug.name AS Drug, l5.code AS `ATC code`, l5.name AS `ATC substance`
ORDER BY Drug
""".strip(),
    },
    {
        "title": "Document audit trail — doc → drug → ATC code",
        "description": "Regulatory documents mentioning the disease, what drug they're about, and its ATC code.",
        "graph": """
MATCH path = (doc:RegulatoryDocument)-[:MENTIONS]->(d:MeshDescriptor {name: '$NAME'})
OPTIONAL MATCH aboutPath = (doc)-[:ABOUT]->(:Drug)-[:HAS_ATC_CODE]->(:ATCLevel5)
RETURN path, aboutPath
""".strip(),
        "preview": """
MATCH (doc:RegulatoryDocument)-[:MENTIONS]->(d:MeshDescriptor {name: $name})
OPTIONAL MATCH (doc)-[:ABOUT]->(drug:Drug)
RETURN doc.title AS Document, doc.date AS Date, drug.name AS Drug, doc.url AS URL
ORDER BY Date DESC LIMIT 100
""".strip(),
    },
]


def _browser_deep_link(cypher: str) -> str:
    """Neo4j Browser deep link: cmd=edit pre-loads the editor (cmd=query is not
    executed by newer Browser versions). One Run click then shows the graph.
    Prepend USE so the query lands on the prototype's database, not the default."""
    full = f"USE {neo4j_settings()['database']}\n{cypher}"
    return f"{neo4j_browser_url()}?cmd=edit&arg={urllib.parse.quote(full)}"


with tab_graph:
    disease = st.text_input("Disease (MeSH descriptor name)", value="Diabetes Mellitus",
                            key="graph_disease")
    choice = st.selectbox("Traversal", [q["title"] for q in TRAVERSAL_QUERIES])
    query = next(q for q in TRAVERSAL_QUERIES if q["title"] == choice)
    st.caption(query["description"])

    if disease:
        exists = run("MATCH (d:MeshDescriptor {name: $name}) RETURN d.ui AS ui",
                     name=disease)
        if not exists:
            st.warning(f"**{disease}** is not a MeSH descriptor name — try the exact "
                       "name shown by the search tab (e.g. 'Diabetes Mellitus').")
        else:
            graph_cypher = query["graph"].replace("$NAME", disease.replace("'", "\\'"))
            st.code(graph_cypher, language="cypher")
            st.link_button("▶️ Open in Neo4j Browser (loads query — hit ▶ Run for graph view)",
                           _browser_deep_link(graph_cypher))
            st.subheader("Preview (table)")
            preview_rows = run(query["preview"], name=disease)
            if preview_rows:
                st.dataframe(pd.DataFrame(preview_rows), hide_index=True)
            else:
                st.info("No results for this disease.")

with tab_safety:
    drugs_with_reports = [r["n"] for r in run(
        "MATCH (d:Drug)-[:HAS_REPORT]->(:AdverseEventReport) "
        "RETURN DISTINCT d.name AS n ORDER BY n"
    )]
    if not drugs_with_reports:
        st.info("No FAERS data yet — run `python -m etl.ingest_faers` first.")
    else:
        drug = st.selectbox("Drug (with FAERS reports)", drugs_with_reports)
        summary = run(
            """
            MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
            RETURN count(r) AS reports,
                   sum(CASE WHEN r.serious THEN 1 ELSE 0 END) AS serious,
                   sum(CASE WHEN r.death THEN 1 ELSE 0 END) AS deaths,
                   sum(CASE WHEN r.hospitalization THEN 1 ELSE 0 END) AS hospitalizations
            """,
            drug=drug,
        )[0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Reports (suspect)", summary["reports"])
        m2.metric("Serious", summary["serious"])
        m3.metric("Deaths", summary["deaths"])
        m4.metric("Hospitalizations", summary["hospitalizations"])

        reactions = run(
            """
            MATCH (d:Drug {name: $drug})-[:HAS_REPORT]->(r:AdverseEventReport)
                  -[:REPORTS_REACTION]->(x:Reaction)
            OPTIONAL MATCH (x)-[:MAPS_TO_MESH]->(m:MeshDescriptor)
            RETURN x.name AS Reaction, count(r) AS Reports,
                   sum(CASE WHEN r.death THEN 1 ELSE 0 END) AS Deaths,
                   coalesce(m.name, '') AS `MeSH descriptor`
            ORDER BY Reports DESC, Deaths DESC LIMIT 15
            """,
            drug=drug,
        )
        left, right = st.columns([1, 1])
        with left:
            st.subheader("Top reactions")
            st.bar_chart(pd.DataFrame(reactions).set_index("Reaction")[["Reports"]])
        with right:
            st.subheader("Reaction detail (with MeSH cross-link)")
            st.dataframe(pd.DataFrame(reactions), hide_index=True)

        safety_graph = f"""
MATCH (d:Drug {{name: '{drug.replace("'", "\\'")}'}})-[:HAS_REPORT]->(r:AdverseEventReport)-[:REPORTS_REACTION]->(x:Reaction)
OPTIONAL MATCH meshPath = (x)-[:MAPS_TO_MESH]->(m:MeshDescriptor)
RETURN d, r, x, meshPath
LIMIT 300
""".strip()
        st.link_button("▶️ Open safety graph in Neo4j Browser",
                       _browser_deep_link(safety_graph))

with st.sidebar:
    db = neo4j_settings()["database"]
    st.markdown(
        f"Explore the raw graph in [Neo4j Browser]({neo4j_browser_url()}) "
        f"— select the **`{db}`** database, or use the deep links in the "
        "🕸️ Graph neighborhood tab (they include `USE` automatically)."
    )
