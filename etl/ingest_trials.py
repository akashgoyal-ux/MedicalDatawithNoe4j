"""Ingest ClinicalTrials.gov studies into Neo4j as pipeline intelligence.

Model:
    (:Trial {nctId, title, phase, status, startDate})
        -[:STUDIES]->(:MeshDescriptor)      -- via derived condition MeSH ids (exact ui join)
        -[:TESTS]->(:Drug)                  -- intervention name match to known drugs
        -[:SPONSORED_BY]->(:Sponsor)

Trials are fetched for the "active" diseases in the graph (descriptors with
documents or indicated drugs), so the pipeline subgraph joins the existing
regulatory graph immediately.

Usage:
    python -m etl.ingest_trials                      # top active diseases, 25 trials each
    python -m etl.ingest_trials --per-disease 50 --max-diseases 20
"""
import argparse
import logging
import time

import requests

from etl.config import clinicaltrials_api_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("ingest_trials")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PAGE_SIZE = 100
REQUEST_DELAY_S = 0.3


def active_diseases(max_diseases: int) -> list[dict]:
    """Mesh descriptors with documents or indicated drugs, most-connected first."""
    return run(
        """
        MATCH (m:MeshDescriptor)
        OPTIONAL MATCH (doc:RegulatoryDocument)-[:MENTIONS]->(m)
        WITH m, count(DISTINCT doc) AS docs
        OPTIONAL MATCH (drug:Drug)-[:INDICATED_FOR]->(m)
        WITH m, docs, count(DISTINCT drug) AS drugs
        WHERE docs > 0 OR drugs > 0
        RETURN m.ui AS ui, m.name AS name, docs, drugs
        ORDER BY docs + drugs DESC LIMIT $n
        """,
        n=max_diseases,
    )


def fetch_trials(condition: str, limit: int) -> list[dict]:
    """Studies whose derived condition browse includes the condition term."""
    params = {
        "query.cond": f'AREA[ConditionSearch] ( {condition} )',
        "pageSize": min(limit, PAGE_SIZE),
        "fields": ("NCTId,BriefTitle,OverallStatus,Phase,StartDate,LeadSponsorName,"
                   "ConditionMeshTerm,ConditionMeshId,InterventionName,InterventionType"),
    }
    r = requests.get(clinicaltrials_api_url(), params=params, timeout=60)
    if r.status_code != 200:
        log.warning("trials fetch failed for %r: %s", condition, r.status_code)
        return []
    time.sleep(REQUEST_DELAY_S)
    return r.json().get("studies", [])[:limit]


def parse_trial(study: dict) -> dict:
    ps = study.get("protocolSection", {})
    derived = study.get("derivedSection", {})
    phases = ps.get("designModule", {}).get("phases") or []
    interventions = [
        i["name"] for i in ps.get("armsInterventionsModule", {}).get("interventions", [])
        if i.get("type") == "DRUG" and i.get("name")
    ]
    return {
        "nctId": ps.get("identificationModule", {}).get("nctId"),
        "title": ps.get("identificationModule", {}).get("briefTitle"),
        "phase": phases[0] if phases else None,
        "status": ps.get("statusModule", {}).get("overallStatus"),
        "startDate": ps.get("statusModule", {}).get("startDateStruct", {}).get("date"),
        "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
        "condition_mesh_uis": [m["id"] for m in
                               derived.get("conditionBrowseModule", {}).get("meshes", [])],
        "interventions": interventions,
    }


def drug_name_lookup() -> dict[str, str]:
    return {r["n"].lower(): r["n"] for r in run("MATCH (d:Drug) RETURN d.name AS n")}


def load_trial(trial: dict, drug_lookup: dict[str, str]) -> bool:
    if not trial["nctId"]:
        return False
    matched_drugs = sorted({drug_lookup[i.lower()] for i in trial["interventions"]
                            if i.lower() in drug_lookup})
    run_write(
        """
        MERGE (t:Trial {nctId: $nctId})
        SET t.title = $title, t.phase = $phase, t.status = $status,
            t.startDate = $startDate
        WITH t
        CALL (t) {
            WITH t
            OPTIONAL MATCH (s0:Sponsor {name: $sponsor})
            FOREACH (_ IN CASE WHEN $sponsor IS NULL THEN [] ELSE [1] END |
                MERGE (s:Sponsor {name: $sponsor})
                MERGE (t)-[:SPONSORED_BY]->(s))
        }
        WITH t
        UNWIND $mesh_uis AS ui
        MATCH (m:MeshDescriptor {ui: ui})
        MERGE (t)-[:STUDIES]->(m)
        WITH DISTINCT t
        UNWIND $drugs AS drugName
        MATCH (d:Drug {name: drugName})
        MERGE (t)-[:TESTS]->(d)
        """,
        nctId=trial["nctId"], title=trial["title"], phase=trial["phase"],
        status=trial["status"], startDate=trial["startDate"],
        sponsor=trial["sponsor"], mesh_uis=trial["condition_mesh_uis"],
        drugs=matched_drugs,
    )
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-disease", type=int, default=25)
    ap.add_argument("--max-diseases", type=int, default=40)
    args = ap.parse_args()

    diseases = active_diseases(args.max_diseases)
    drug_lookup = drug_name_lookup()
    log.info("fetching trials for %d active diseases", len(diseases))

    loaded = 0
    for disease in diseases:
        studies = fetch_trials(disease["name"], args.per_disease)
        n = sum(load_trial(parse_trial(s), drug_lookup) for s in studies)
        loaded += n
        log.info("%-40s fetched=%3d loaded=%3d", disease["name"], len(studies), n)

    stats = run("""
        MATCH (t:Trial) WITH count(t) AS trials
        MATCH (s:Sponsor) WITH trials, count(s) AS sponsors
        MATCH ()-[e:STUDIES]->() WITH trials, sponsors, count(e) AS studyEdges
        MATCH ()-[x:TESTS]->() RETURN trials, sponsors, studyEdges, count(x) AS testEdges
    """)[0]
    log.info("done: %d trials loaded. Totals: %s", loaded, stats)


if __name__ == "__main__":
    main()
