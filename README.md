# Medical Data with Neo4j — Prototype

A working prototype of the architecture from the talk
[Enhancing Medical Data Processing with Neo4j](https://www.youtube.com/watch?v=6JMMip1FTxY)
(see [implementation-plan.md](./implementation-plan.md) and [transcript.md](./transcript.md)):

- **WHO ATC classification** loaded as a 5-level tree (drug "biographies" — e.g. `C03XA01` = tolvaptan).
- **NLM MeSH 2026** loaded as a multi-parent DAG (descriptors, tree numbers, entry-term synonyms).
- **FDA drug labels** (openFDA) ingested as regulatory documents, auto-tagged with MeSH terms
  via Aho-Corasick dictionary matching, linked to drugs and ATC codes.
- **FAERS adverse events** (openFDA `drug/event`) ingested per drug: reports → suspect drugs →
  reactions (MedDRA PTs), with reactions auto-cross-linked to MeSH descriptors when names match.
- **Graph-assisted search expansion**: user term → MeSH traversal → expanded term set →
  document retrieval, reproducing the talk's "search diabetes mellitus, find every agency's
  terminology" behavior.

## Stack

- **Python 3.11+** (ETL, FastAPI service, Streamlit demo — one language throughout)
- **Neo4j Desktop** local DBMS (Enterprise/Community 5.x+; this prototype used 2026.03)
- Data: WHOCC ATC/DDD Index, NLM MeSH XML, openFDA `drug/label` — all public

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then set NEO4J_PASSWORD to your Neo4j Desktop DBMS password
```

In Neo4j Desktop: create/start a local DBMS, then (optional, keeps your other data isolated):

```bash
.venv/bin/python -c "
from etl.neo4j_client import get_driver
get_driver().execute_query('CREATE DATABASE medical IF NOT EXISTS', database_='system')"
```

## Load the graph

```bash
.venv/bin/python -m etl.init_db                       # constraints + full-text indexes
.venv/bin/python -m etl.load_atc --groups C A         # ATC subset (C=cardio, A=alimentary)
                                                      # omit --groups for all 14 (~1500 pages, slow)
.venv/bin/python -m etl.load_mesh                     # full MeSH 2026 (~30k descriptors, ~300k terms)
.venv/bin/python -m etl.ingest_openfda --limit 250    # FDA labels, MeSH-tagged
.venv/bin/python -m etl.ingest_faers                  # FAERS adverse events for graphed drugs
```

## Run

```bash
.venv/bin/uvicorn api.main:app --port 8000            # API: /docs, /expand, /atc/{code}, /search, /safety/{drug}
.venv/bin/streamlit run app/streamlit_app.py          # demo UI on :8501
```

Neo4j Browser at http://localhost:7474 (select the `medical` database) for raw exploration.

## Demo scenarios (from the talk)

1. **Decode a drug's ATC biography**: `GET /atc/C03XA01` →
   Cardiovascular system → Diuretics → Other diuretics → Vasopressin antagonists → tolvaptan.
2. **Expand a search term**: `GET /expand?term=Diabetes Mellitus` → descriptor + synonyms +
   all descendants (Type 1, Type 2, Diabetes Complications → Diabetic Angiopathies →
   Diabetic Foot / Diabetic Retinopathy).
3. **Search documents across terminology**: `GET /search?term=Diabetes Mellitus` returns
   both the exact-match baseline count and the graph-expanded recall.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Layout

```
etl/    config (.env), Neo4j client, schema, ATC loader, MeSH loader, openFDA ingest
api/    FastAPI expansion/search service
app/    Streamlit demo (MeSH-expanded search + ATC decoder tabs)
tests/  offline unit tests for parsers
```
