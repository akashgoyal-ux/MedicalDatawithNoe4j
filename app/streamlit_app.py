"""Streamlit demo UI for the graph-assisted regulatory search prototype.

Run: streamlit run app/streamlit_app.py
Tabs:
  1. Search — type a disease term, see MeSH expansion + matching documents
  2. ATC decoder — type a code like C03XA01, see the 5-level breakdown
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from api.main import expand_term
from etl.neo4j_client import run

st.set_page_config(page_title="Regulatory Intelligence Prototype", layout="wide")
st.title("🧬 Regulatory Intelligence Prototype")
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


tab_search, tab_atc = st.tabs(["🔍 MeSH-expanded search", "💊 ATC code decoder"])

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

st.sidebar.markdown(
    "Explore the raw graph in **Neo4j Browser** (`http://localhost:7474`) — try:\n"
    "```cypher\n"
    "MATCH p=(m:MeshDescriptor {name:'Diabetes Mellitus'})-[:BROADER_THAN*1..2]->(c)\n"
    "RETURN p LIMIT 100\n"
    "```"
)
