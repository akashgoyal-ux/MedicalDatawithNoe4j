# MedGraph — Medical Data with Neo4j

MedGraph: a working prototype of the architecture from the talk
[Enhancing Medical Data Processing with Neo4j](https://www.youtube.com/watch?v=6JMMip1FTxY)
(see [implementation-plan.md](./implementation-plan.md) and [transcript.md](./transcript.md)) —
read the full write-up in [docs/blog.md](./docs/blog.md):

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

- **Python 3.11+** (ETL, FastAPI service, LangGraph chatbot, Streamlit demo)
- **React (Vite)** business dashboard in `web/`
- **Neo4j Desktop** local DBMS (Enterprise 2026.03 — used for vector indexes)
- **Ollama** (local): `nomic-embed-text` for embeddings, `llama3.2:3b` for chat
- **Ontology-governed schema**: `ontology/medical_graph.ttl` (RDFS via rdflib) is the
  single source of truth for all node labels / relationship types — enforced by
  `tests/test_ontology.py`
- Data: WHOCC ATC/DDD Index, NLM MeSH XML, openFDA labels + FAERS, ClinicalTrials.gov,
  RxNorm (RxNav), ICD-10-CM (NLM Clinical Tables) — all public, no licenses needed

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
.venv/bin/python -m etl.ingest_trials                 # ClinicalTrials.gov pipeline
.venv/bin/python -m etl.load_rxnorm                   # brand/ingredient aliases + ATC backfill
.venv/bin/python -m etl.load_icd                      # ICD-10-CM alignment for active diseases
.venv/bin/python -m etl.compute_similarity            # drug SIMILAR_TO edges (repurposing)
.venv/bin/python -m etl.embed_nodes                   # Ollama embeddings + vector indexes
.venv/bin/python -m etl.watch                         # diff vs last openFDA pull (weekly cron)
```

Prerequisite for embeddings + chat: `ollama serve` plus
`ollama pull nomic-embed-text && ollama pull llama3.2:3b`.

## Run

```bash
.venv/bin/uvicorn api.main:app --port 8000            # REST API (/docs lists all endpoints)
cd web && npm install && npm run dev                  # React dashboard on :5173
.venv/bin/streamlit run app/streamlit_app.py          # (optional) Streamlit demo on :8501
```

Chat: `POST /chat` or the **Ask Ria** tab — a LangGraph agent (local llama3.2) with tools
for MeSH expansion, document search, ATC decode, FAERS safety, trials, similarity, and
vector semantic search.

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
ontology/ medical_graph.ttl — RDFS schema of every node/edge type (rdflib-parsed)
etl/      config (.env), Neo4j client, ontology registry, loaders (ATC, MeSH,
          openFDA labels, FAERS, trials, RxNorm, ICD-10), similarity, embeddings, watch
api/      FastAPI: /expand /search /atc /safety /trials /timeline /similar /vsearch /chat
chat/     LangGraph ReAct agent (Ollama) with graph tools
app/      Streamlit demo (search, ATC, graph neighborhood w/ Browser deep links, safety)
web/      React dashboard (Search · Pipeline · Safety · ATC · Ask Ria)
tests/    offline unit tests + ontology governance tests
```
