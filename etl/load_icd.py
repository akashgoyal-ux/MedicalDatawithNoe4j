"""Align ICD-10-CM clinical codes to MeSH descriptors (prototype alignment).

Uses the NLM Clinical Tables API for ICD-10-CM (free, no key). For each
"active" disease descriptor, search ICD-10-CM by descriptor name and keep
the top hit as an alignment:

    (:ICD10Code {code, name})-[:ALIGNS_WITH]->(:MeshDescriptor)

This is a name-based heuristic — production alignment should use the UMLS
Metathesaurus cross-walk (free license).

Usage: python -m etl.load_icd [--max-diseases 100]
"""
import argparse
import logging
import time

import requests

from etl.config import icd10cm_api_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("load_icd")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REQUEST_DELAY_S = 0.25


def search_icd10(term: str) -> tuple[str, str] | None:
    """Top ICD-10-CM hit for a term: (code, display name) or None."""
    r = requests.get(icd10cm_api_url(),
                     params={"terms": term, "sf": "code,name", "maxList": 1},
                     timeout=30)
    time.sleep(REQUEST_DELAY_S)
    if r.status_code != 200:
        return None
    total, codes, _, names = r.json()
    if not codes:
        return None
    display = names[0][1] if names and len(names[0]) > 1 else codes[0]
    return codes[0], display


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-diseases", type=int, default=100)
    args = ap.parse_args()

    diseases = run(
        """
        MATCH (m:MeshDescriptor)
        WHERE EXISTS { (:RegulatoryDocument)-[:MENTIONS]->(m) }
           OR EXISTS { (:Drug)-[:INDICATED_FOR]->(m) }
           OR EXISTS { (:Trial)-[:STUDIES]->(m) }
        RETURN m.ui AS ui, m.name AS name
        ORDER BY m.name LIMIT $n
        """,
        n=args.max_diseases,
    )
    log.info("aligning ICD-10-CM for %d active descriptors", len(diseases))

    aligned = 0
    for d in diseases:
        hit = search_icd10(d["name"])
        if not hit:
            continue
        code, icd_name = hit
        run_write(
            """
            MERGE (c:ICD10Code {code: $code})
            SET c.name = $icd_name
            WITH c
            MATCH (m:MeshDescriptor {ui: $ui})
            MERGE (c)-[:ALIGNS_WITH]->(m)
            """,
            code=code, icd_name=icd_name, ui=d["ui"],
        )
        aligned += 1
    log.info("done: %d/%d descriptors aligned to ICD-10-CM", aligned, len(diseases))


if __name__ == "__main__":
    main()
