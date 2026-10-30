"""Embed key node text with a local Ollama model and create Neo4j vector indexes.

Embedded sets (kept prototype-sized on purpose):
  - Drug names + aliases          -> vector index drugEmbedding
  - RegulatoryDocument titles     -> vector index docEmbedding
  - Reaction names                -> vector index reactionEmbedding
  - Active MeshDescriptor names   -> vector index meshEmbedding
    (only descriptors connected to docs/drugs/trials, not all 31k)

Usage: python -m etl.embed_nodes [--model nomic-embed-text]
"""
import argparse
import logging
import os

import requests

from etl.neo4j_client import run, run_write

log = logging.getLogger("embed_nodes")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_URL = f"{OLLAMA_BASE}/api/embed"
DIMENSIONS = 768  # nomic-embed-text
BATCH = 64

# Every text-bearing node class gets embedded; one vector index per label.
SETS = {
    "Drug": {"text": "n.name", "index": "drugEmbedding"},
    "DrugAlias": {"text": "n.name", "index": "aliasEmbedding"},
    "RegulatoryDocument": {"text": "n.title", "index": "docEmbedding"},
    "Reaction": {"text": "n.name", "index": "reactionEmbedding"},
    "MeshDescriptor": {"text": "n.name", "index": "meshEmbedding"},
    "MeshTerm": {"text": "n.name", "index": "termEmbedding"},
    "Trial": {"text": "n.title", "index": "trialEmbedding"},
    "Sponsor": {"text": "n.name", "index": "sponsorEmbedding"},
    "Agency": {"text": "n.name", "index": "agencyEmbedding"},
    "ATCLevel1": {"text": "n.name", "index": "atc1Embedding"},
    "ATCLevel2": {"text": "n.name", "index": "atc2Embedding"},
    "ATCLevel3": {"text": "n.name", "index": "atc3Embedding"},
    "ATCLevel4": {"text": "n.name", "index": "atc4Embedding"},
    "ATCLevel5": {"text": "n.name", "index": "atc5Embedding"},
}


def embed(texts: list[str], model: str) -> list[list[float]]:
    r = requests.post(OLLAMA_EMBED_URL, json={"model": model, "input": texts}, timeout=300)
    r.raise_for_status()
    return [e for e in r.json()["embeddings"]]


def create_indexes() -> None:
    for label, cfg in SETS.items():
        run_write(
            f"""
            CREATE VECTOR INDEX {cfg["index"]} IF NOT EXISTS
            FOR (n:{label}) ON n.embedding
            OPTIONS {{indexConfig: {{`vector.dimensions`: {DIMENSIONS},
                                     `vector.similarity_function`: 'cosine'}}}}
            """
        )
        log.info("vector index ready: %s", cfg["index"])


def embed_set(label: str, cfg: dict, model: str) -> int:
    rows = [r for r in run(
        f"MATCH (n:{label}) WHERE n.embedding IS NULL "
        f"RETURN elementId(n) AS id, {cfg['text']} AS text"
    ) if r["text"]]
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        vectors = embed([r["text"] for r in chunk], model)
        run_write(
            f"""
            UNWIND $rows AS row
            MATCH (n:{label}) WHERE elementId(n) = row.id
            SET n.embedding = row.embedding
            """,
            rows=[{"id": r["id"], "embedding": v} for r, v in zip(chunk, vectors)],
        )
        done += len(chunk)
        log.info("%s: embedded %d/%d", label, done, len(rows))
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nomic-embed-text")
    args = ap.parse_args()
    create_indexes()
    total = 0
    for label, cfg in SETS.items():
        total += embed_set(label, cfg, args.model)
    log.info("done: %d nodes embedded with %s", total, args.model)


if __name__ == "__main__":
    main()
