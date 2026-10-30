"""RxNorm cross-walk: brand/ingredient aliases + ATC backfill for Drug nodes.

Uses the free RxNav REST API (no key). For each Drug in the graph:
  1. rxcui.json?name=<drug>&search=0          -> exact RxNorm concept
  2. rxcui/<rxcui>/related.json?tty=BN+IN+MIN -> brand names / ingredients
  3. rxcui/<rxcui>/property.json?propName=ATC -> ATC code backfill

Model:
    (:DrugAlias {name, tty})-[:ALIAS_OF]->(:Drug)
    Drug.atcCodeRxnorm property set when RxNorm knows an ATC code
    (linked to ATCLevel5 with HAS_ATC_CODE only if that node is loaded).

Usage: python -m etl.load_rxnorm
"""
import logging
import time

import requests

from etl.config import rxnav_api_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("load_rxnorm")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REQUEST_DELAY_S = 0.25


def _get(path: str, **params) -> dict | None:
    r = requests.get(f"{rxnav_api_url()}/{path}", params=params, timeout=30)
    time.sleep(REQUEST_DELAY_S)
    if r.status_code != 200:
        return None
    return r.json()


def find_rxcui(name: str) -> str | None:
    """Exact-match first, then approximate."""
    for search in ("0", "2"):
        data = _get("rxcui.json", name=name, search=search)
        ids = (data or {}).get("idGroup", {}).get("rxnormId") or []
        if ids:
            return ids[0]
    return None


def related_aliases(rxcui: str) -> list[dict]:
    """Brand names (BN), ingredients (IN), multiple-ingredient combos (MIN).

    RxNav needs a literal '+' in tty (it means OR); passing it via params
    would URL-encode it to %2B and the API returns 400.
    """
    data = _get(f"rxcui/{rxcui}/related.json?tty=BN+IN+MIN")
    out = []
    for group in (data or {}).get("relatedGroup", {}).get("conceptGroup", []):
        for prop in group.get("conceptProperties", []):
            out.append({"name": prop["name"], "tty": group.get("tty")})
    return out


def atc_property(rxcui: str) -> str | None:
    data = _get(f"rxcui/{rxcui}/property.json", propName="ATC")
    concepts = (data or {}).get("propConceptGroup", {}).get("propConcept") or []
    return concepts[0]["propValue"] if concepts else None


def load_for_drug(drug_name: str) -> dict:
    rxcui = find_rxcui(drug_name)
    if not rxcui:
        return {"drug": drug_name, "rxcui": None, "aliases": 0, "atc": None}
    aliases = [a for a in related_aliases(rxcui) if a["name"].lower() != drug_name.lower()]
    atc = atc_property(rxcui)
    run_write(
        """
        MATCH (d:Drug {name: $drug})
        SET d.rxcui = $rxcui
        WITH d
        CALL (d) {
            WITH d
            FOREACH (_ IN CASE WHEN $atc IS NULL THEN [] ELSE [1] END |
                SET d.atcCodeRxnorm = $atc)
        }
        WITH d
        UNWIND $aliases AS alias
        MERGE (a:DrugAlias {name: alias.name})
        SET a.tty = alias.tty
        MERGE (a)-[:ALIAS_OF]->(d)
        """,
        drug=drug_name, rxcui=rxcui, atc=atc, aliases=aliases,
    )
    if atc:
        run_write(
            """
            MATCH (d:Drug {name: $drug}), (l5:ATCLevel5 {code: $atc})
            MERGE (d)-[:HAS_ATC_CODE]->(l5)
            MERGE (l5)-[:CLASSIFIES]->(d)
            """,
            drug=drug_name, atc=atc,
        )
    return {"drug": drug_name, "rxcui": rxcui, "aliases": len(aliases), "atc": atc}


def main() -> None:
    drugs = [r["n"] for r in run("MATCH (d:Drug) RETURN d.name AS n ORDER BY n")]
    log.info("resolving %d drugs via RxNorm", len(drugs))
    found = 0
    for name in drugs:
        result = load_for_drug(name)
        if result["rxcui"]:
            found += 1
            log.info("%-25s rxcui=%s aliases=%d atc=%s",
                     name, result["rxcui"], result["aliases"], result["atc"])
        else:
            log.info("%-25s no RxNorm match", name)
    stats = run("""
        MATCH (a:DrugAlias) WITH count(a) AS aliases
        MATCH (d:Drug) WHERE d.rxcui IS NOT NULL
        RETURN aliases, count(d) AS resolved
    """)[0]
    log.info("done: %d/%d drugs resolved, %d aliases. Totals: %s",
             found, len(drugs), stats["aliases"], stats)


if __name__ == "__main__":
    main()
