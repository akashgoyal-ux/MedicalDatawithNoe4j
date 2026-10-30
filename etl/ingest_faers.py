"""Ingest openFDA FAERS adverse-event reports into Neo4j.

Model:
    (:Drug)-[:HAS_REPORT]->(:AdverseEventReport {reportId, receiveDate, serious, death, ...})
    (:AdverseEventReport)-[:REPORTS_REACTION]->(:Reaction)  -- MedDRA preferred terms
    (:Reaction)-[:MAPS_TO_MESH]->(:MeshDescriptor)  -- exact name cross-link
    (suspect drug only: FAERS drugcharacterization=1)

Reports are fetched for drugs already in the graph (ATC-matched first), so the
safety subgraph joins the existing vocabulary/document graph immediately.

Note: reaction terms are MedDRA PTs. MedDRA is licensed; we store term strings
as nodes without redistributing the MedDRA hierarchy.

Usage:
    python -m etl.ingest_faers                 # all ATC-matched drugs, 50 reports each
    python -m etl.ingest_faers --per-drug 100 --drugs Glipizide Metformin
"""
import argparse
import logging
import re
import time

import requests

from etl.config import openfda_event_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("ingest_faers")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PAGE_SIZE = 100  # openFDA max
REQUEST_DELAY_S = 0.3

SERIOUSNESS_FLAGS = {
    "death": "seriousnessdeath",
    "life_threatening": "seriousnesslifethreatening",
    "hospitalization": "seriousnesshospitalization",
    "disabling": "seriousnessdisabling",
    "congenital_anomaly": "seriousnesscongenitalanomali",
    "other_serious": "seriousnessother",
}

# trailing strength/dose-form noise on medicinalproduct strings: "DURAGESIC-100",
# "IBUPROFEN 200 MG", "GLIPIZIDE XL"
STRENGTH_RE = re.compile(r"[-\s]+(\d+(\.\d+)?\s*(mg|mcg|g|ml|%|iu|unit(s)?|meq)\b.*)$", re.I)
TRAILING_DOSE_RE = re.compile(r"-\d+(\.\d+)?$")
SUFFIX_RE = re.compile(r"[-\s]+(xl|er|xr|sr|cr|la|dr|hct|hctz|odt|pm|forte|plus)$", re.I)


def normalize_product_name(name: str) -> str:
    """'GLIPIZIDE XL' / 'DURAGESIC-100' -> 'glipizide' / 'duragesic'."""
    n = STRENGTH_RE.sub("", name.strip())
    n = TRAILING_DOSE_RE.sub("", n)
    n = SUFFIX_RE.sub("", n)
    return n.strip(" -").lower()


def iso_date(yyyymmdd: str | None) -> str | None:
    if yyyymmdd and len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return None


def extract_suspect_names(report: dict) -> list[str]:
    """Generic/openfda names of suspect drugs (characterization=1), else first drug."""
    drugs = report.get("patient", {}).get("drug") or []
    suspects = [d for d in drugs if d.get("drugcharacterization") == "1"] or drugs[:1]
    names: list[str] = []
    for d in suspects:
        generics = (d.get("openfda") or {}).get("generic_name") or []
        if generics:
            names.extend(generics)
        elif d.get("medicinalproduct"):
            names.append(normalize_product_name(d["medicinalproduct"]))
    return names


def extract_reactions(report: dict) -> list[str]:
    reactions = report.get("patient", {}).get("reaction") or []
    return sorted({(r.get("reactionmeddrapt") or "").strip().title()
                   for r in reactions if r.get("reactionmeddrapt")})


def fetch_reports_for_drug(drug_name: str, limit: int) -> list[dict]:
    """Try openfda.generic_name first; fall back to medicinalproduct."""
    for field, value in (("patient.drug.openfda.generic_name", drug_name),
                         ("patient.drug.medicinalproduct", drug_name)):
        params = {"search": f'{field}:"{value}"', "limit": min(limit, PAGE_SIZE)}
        r = requests.get(openfda_event_url(), params=params, timeout=60)
        if r.status_code == 404:
            time.sleep(REQUEST_DELAY_S)
            continue
        r.raise_for_status()
        time.sleep(REQUEST_DELAY_S)
        return r.json()["results"][:limit]
    return []


def graph_drug_names() -> dict[str, str]:
    """lowercase name -> canonical Drug.name for drugs that have an ATC code."""
    return {r["n"].lower(): r["n"] for r in
            run("MATCH (d:Drug)-[:HAS_ATC_CODE]->() RETURN d.name AS n")}


def mesh_name_lookup() -> dict[str, str]:
    """lowercase term -> descriptor ui (descriptors + entry terms)."""
    rows = run("""
        MATCH (d:MeshDescriptor) RETURN toLower(d.name) AS name, d.ui AS ui
        UNION ALL
        MATCH (t:MeshTerm)-[:SYNONYM_OF]->(d:MeshDescriptor)
        RETURN toLower(t.name) AS name, d.ui AS ui
    """)
    return {r["name"]: r["ui"] for r in rows}


def load_reports(reports: list[dict], drug_lookup: dict[str, str],
                 mesh_lookup: dict[str, str]) -> tuple[int, int]:
    loaded, linked = 0, 0
    for report in reports:
        report_id = report.get("safetyreportid")
        if not report_id:
            continue
        suspect_names = extract_suspect_names(report)
        matched_drugs = sorted({drug_lookup[n.lower()] for n in suspect_names
                                if n.lower() in drug_lookup})
        if not matched_drugs:
            continue
        reactions = extract_reactions(report)
        flags = {k: (report.get(f) == "1") for k, f in SERIOUSNESS_FLAGS.items()}

        run_write(
            """
            MERGE (r:AdverseEventReport {reportId: $report_id})
            SET r.receiveDate = $date, r.serious = $serious,
                r.death = $death, r.lifeThreatening = $life_threatening,
                r.hospitalization = $hospitalization, r.disabling = $disabling,
                r.congenitalAnomaly = $congenital_anomaly, r.otherSerious = $other_serious
            WITH r
            UNWIND $drugs AS drugName
            MATCH (d:Drug {name: drugName})
            MERGE (d)-[:HAS_REPORT]->(r)
            """,
            report_id=report_id, date=iso_date(report.get("receivedate")),
            serious=report.get("serious") == "1", drugs=matched_drugs, **flags,
        )
        for reaction in reactions:
            ui = mesh_lookup.get(reaction.lower())
            run_write(
                """
                MATCH (r:AdverseEventReport {reportId: $report_id})
                MERGE (x:Reaction {name: $reaction})
                MERGE (r)-[:REPORTS_REACTION]->(x)
                WITH x
                CALL (x) {
                    WITH x
                    OPTIONAL MATCH (m:MeshDescriptor {ui: $ui})
                    FOREACH (_ IN CASE WHEN $ui IS NULL THEN [] ELSE [1] END |
                        MERGE (x)-[:MAPS_TO_MESH]->(m))
                }
                RETURN x
                """,
                report_id=report_id, reaction=reaction, ui=ui,
            )
        loaded += 1
        linked += len(reactions)
    return loaded, linked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-drug", type=int, default=50)
    ap.add_argument("--drugs", nargs="*", default=None,
                    help="drug names to fetch (default: all ATC-matched drugs)")
    args = ap.parse_args()

    drug_lookup = graph_drug_names()
    targets = args.drugs or sorted(drug_lookup.values())
    mesh_lookup = mesh_name_lookup()
    log.info("fetching FAERS for %d drugs (%d reports each)", len(targets), args.per_drug)

    total_reports, total_reactions = 0, 0
    for name in targets:
        reports = fetch_reports_for_drug(name.upper(), args.per_drug)
        loaded, linked = load_reports(reports, drug_lookup, mesh_lookup)
        total_reports += loaded
        total_reactions += linked
        log.info("%-22s fetched=%3d loaded=%3d reactions=%3d",
                 name, len(reports), loaded, linked)

    stats = run("""
        MATCH (r:AdverseEventReport) WITH count(r) AS reports
        MATCH (x:Reaction) WITH reports, count(x) AS reactions
        MATCH ()-[e:REPORTS_REACTION]->() WITH reports, reactions, count(e) AS edges
        MATCH ()-[m:MAPS_TO_MESH]->() RETURN reports, reactions, edges, count(m) AS meshLinks
    """)[0]
    log.info("done: %d new reports, %d reaction links. Totals: %s",
             total_reports, total_reactions, stats)


if __name__ == "__main__":
    main()
