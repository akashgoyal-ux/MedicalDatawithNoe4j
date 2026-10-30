"""Ingest FDA drug labels from openFDA into Neo4j as RegulatoryDocuments.

- Creates (:RegulatoryDocument)-[:ISSUED_BY]->(:Agency {name:'FDA'})
- Creates (:Drug) from generic_name and links doc -[:ABOUT]-> drug
- Tags documents with MeSH descriptors found in indications text via
  Aho-Corasick dictionary matching against MeshDescriptor + MeshTerm names
- Links Drug -[:HAS_ATC_CODE]-> ATCLevel5 by matching the generic name to
  loaded ATC level-5 substance names

Usage:
    python -m etl.ingest_openfda --limit 250
"""
import argparse
import logging
import re

import ahocorasick
import requests

from etl.config import dailymed_drug_url, openfda_label_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("ingest_openfda")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PAGE_SIZE = 100  # openFDA max per request
MIN_TERM_LEN = 4  # skip ultra-short terms that cause false positives
WORD = re.compile(r"[a-z0-9]+")


def fetch_labels(limit: int) -> list[dict]:
    results = []
    for skip in range(0, limit, PAGE_SIZE):
        params = {
            "search": "_exists_:indications_and_usage AND _exists_:openfda.generic_name",
            "limit": min(PAGE_SIZE, limit - skip),
            "skip": skip,
        }
        r = requests.get(openfda_label_url(), params=params, timeout=60)
        if r.status_code == 404:
            break
        r.raise_for_status()
        results.extend(r.json()["results"])
        log.info("fetched %d/%d labels", len(results), limit)
    return results


def build_mesh_automaton() -> ahocorasick.Automaton:
    """Aho-Corasick automaton: normalized term -> descriptor ui."""
    rows = run(
        """
        MATCH (d:MeshDescriptor)
        RETURN d.ui AS ui, d.name AS name
        UNION ALL
        MATCH (t:MeshTerm)-[:SYNONYM_OF]->(d:MeshDescriptor)
        RETURN d.ui AS ui, t.name AS name
        """
    )
    automaton = ahocorasick.Automaton()
    seen = set()
    for row in rows:
        norm = normalize(row["name"])
        if len(norm) < MIN_TERM_LEN:
            continue
        key = (norm, row["ui"])
        if key in seen:
            continue
        seen.add(key)
        automaton.add_word(norm, (norm, row["ui"]))
    automaton.make_automaton()
    log.info("automaton built with %d term entries", len(seen))
    return automaton


def normalize(text: str) -> str:
    return " ".join(WORD.findall(text.lower()))


def tag_document(automaton: ahocorasick.Automaton, text: str) -> list[str]:
    norm_text = f" {normalize(text)} "  # pad so whole-phrase boundary check works
    found = set()
    for _, (term, ui) in automaton.iter(norm_text):
        found.add(ui)
    return sorted(found)


def atc_lookup() -> dict[str, str]:
    """substance name (lowercase) -> ATC L5 code, from what's already loaded."""
    return {
        r["name"].lower(): r["code"]
        for r in run("MATCH (l:ATCLevel5) RETURN l.code AS code, l.name AS name")
    }


def load_labels(labels: list[dict]) -> None:
    automaton = build_mesh_automaton()
    atc_by_name = atc_lookup()
    log.info("%d ATC substance names available for drug matching", len(atc_by_name))

    run_write("MERGE (:Agency {name: 'FDA'})")

    tagged_docs = 0
    matched_drugs = 0
    for label in labels:
        openfda = label.get("openfda", {})
        generics = openfda.get("generic_name") or []
        if not generics:
            continue
        drug_name = generics[0].strip().title()
        doc_id = label["set_id"]
        indications = " ".join(label.get("indications_and_usage") or [])
        brands = openfda.get("brand_name") or []
        title = brands[0].title() if brands else drug_name
        date_raw = label.get("effective_time", "")
        date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}" if len(date_raw) == 8 else None
        url = dailymed_drug_url(doc_id)

        mesh_uis = tag_document(automaton, indications)
        if mesh_uis:
            tagged_docs += 1
        atc_code = atc_by_name.get(drug_name.lower())

        run_write(
            """
            MERGE (doc:RegulatoryDocument {docId: $doc_id})
            SET doc.title = $title, doc.date = $date, doc.url = $url
            WITH doc
            MATCH (a:Agency {name: 'FDA'})
            MERGE (doc)-[:ISSUED_BY]->(a)
            MERGE (drug:Drug {name: $drug_name})
            MERGE (doc)-[:ABOUT]->(drug)
            WITH doc, drug
            UNWIND $mesh_uis AS ui
            MATCH (m:MeshDescriptor {ui: ui})
            MERGE (doc)-[:MENTIONS]->(m)
            MERGE (drug)-[:INDICATED_FOR]->(m)
            """,
            doc_id=doc_id, title=title, date=date, url=url,
            drug_name=drug_name, mesh_uis=mesh_uis,
        )
        if atc_code:
            matched_drugs += 1
            run_write(
                """
                MATCH (drug:Drug {name: $drug_name})
                MATCH (l5:ATCLevel5 {code: $atc_code})
                MERGE (l5)-[:CLASSIFIES]->(drug)
                MERGE (drug)-[:HAS_ATC_CODE]->(l5)
                """,
                drug_name=drug_name, atc_code=atc_code,
            )

    log.info("done: %d labels, %d docs MeSH-tagged, %d drugs ATC-matched",
             len(labels), tagged_docs, matched_drugs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    args = ap.parse_args()
    labels = fetch_labels(args.limit)
    load_labels(labels)


if __name__ == "__main__":
    main()
