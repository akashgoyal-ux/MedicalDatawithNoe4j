"""Watch mode: re-pull openFDA labels and diff against the graph.

Reloads the same label set as ingest_openfda and reports documents that are
new since the last ingest (by docId). Run on a schedule (e.g. weekly cron) to
catch new approvals and label revisions.

Usage:
    python -m etl.watch --limit 250
"""
import argparse
import logging

from etl.ingest_openfda import fetch_labels
from etl.neo4j_client import run

log = logging.getLogger("watch")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    args = ap.parse_args()

    known = {r["id"] for r in
             run("MATCH (d:RegulatoryDocument) RETURN d.docId AS id")}
    labels = fetch_labels(args.limit)
    current = {l["set_id"] for l in labels}

    new_docs = current - known
    gone_docs = known - current
    log.info("known in graph: %d | fetched now: %d", len(known), len(current))
    log.info("NEW documents: %d", len(new_docs))
    for doc_id in sorted(new_docs):
        label = next(l for l in labels if l["set_id"] == doc_id)
        names = (label.get("openfda", {}).get("brand_name")
                 or label.get("openfda", {}).get("generic_name") or ["?"])
        log.info("  + %s (%s)", names[0], doc_id[:8])
    if gone_docs:
        log.info("no longer returned by API: %d", len(gone_docs))
    if new_docs:
        log.info("run `python -m etl.ingest_openfda --limit %d` to load them",
                 args.limit)


if __name__ == "__main__":
    main()
