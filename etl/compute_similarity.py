"""Compute drug-drug similarity edges in pure Cypher (no GDS plugin needed).

Two signals, combined into one weighted SIMILAR_TO edge:
  - shared MeSH indications  (drugs treating the same diseases)
  - shared FAERS reactions   (drugs with the same adverse-event profile)

    (:Drug)-[:SIMILAR_TO {sharedIndications, sharedReactions, score}]-(:Drug)

Shared-indication pairs are a first-pass drug-repurposing signal: if drug A
and drug B share most indications, either is a candidate for the other's
off-label territory.

Usage: python -m etl.compute_similarity [--min-indications 2]
"""
import argparse
import logging

from etl.neo4j_client import run, run_write

log = logging.getLogger("compute_similarity")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-indications", type=int, default=1)
    args = ap.parse_args()

    run_write("MATCH ()-[e:SIMILAR_TO]-() DELETE e")

    run_write(
        """
        MATCH (a:Drug)-[:INDICATED_FOR]->(m:MeshDescriptor)<-[:INDICATED_FOR]-(b:Drug)
        WHERE elementId(a) < elementId(b)
        WITH a, b, count(DISTINCT m) AS sharedInd
        WHERE sharedInd >= $min_ind
        OPTIONAL MATCH (a)<-[:HAS_REPORT]-(:AdverseEventReport)-[:REPORTS_REACTION]->
                       (x:Reaction)<-[:REPORTS_REACTION]-(:AdverseEventReport)-[:HAS_REPORT]->(b)
        WITH a, b, sharedInd, count(DISTINCT x) AS sharedRxn
        MERGE (a)-[e:SIMILAR_TO]-(b)
        SET e.sharedIndications = sharedInd, e.sharedReactions = sharedRxn,
            e.score = sharedInd * 2 + sharedRxn
        """,
        min_ind=args.min_indications,
    )
    stats = run("""
        MATCH ()-[e:SIMILAR_TO]-() WITH count(e) / 2 AS pairs
        MATCH (a:Drug)-[e:SIMILAR_TO]-(b:Drug)
        WHERE elementId(a) < elementId(b)
        RETURN pairs, collect({a: a.name, b: b.name, ind: e.sharedIndications,
                               rxn: e.sharedReactions, score: e.score})[..10] AS top
    """)[0]
    log.info("SIMILAR_TO pairs: %s", stats["pairs"])
    for pair in stats["top"]:
        log.info("  %s <-> %s (ind=%d rxn=%d score=%d)",
                 pair["a"], pair["b"], pair["ind"], pair["rxn"], pair["score"])


if __name__ == "__main__":
    main()
