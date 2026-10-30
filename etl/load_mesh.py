"""Load NLM MeSH descriptors, tree hierarchy (DAG), and entry terms into Neo4j.

Source: 2026 MeSH XML (desc2026.gz) from
https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/

Modelled as a DAG: one descriptor can have multiple tree numbers
(e.g. Diabetes Mellitus = C18.452.394.750 and C19.246), each yielding a
BROADER_THAN edge to the parent descriptor for that tree location.

Usage:
    python -m etl.load_mesh                # download if needed, parse, load
    python -m etl.load_mesh --limit 2000   # prototype subset
"""
import argparse
import gzip
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from etl.config import mesh_xml_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("load_mesh")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DATA_DIR = Path("data/mesh")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _paths() -> tuple[Path, Path]:
    filename = mesh_xml_url().rsplit("/", 1)[-1]  # e.g. desc2026.gz
    gz_path = DATA_DIR / filename
    xml_path = DATA_DIR / filename.removesuffix(".gz")
    return gz_path, xml_path

BATCH = 2000


def _is_gzip(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(2) == b"\x1f\x8b"


def download() -> Path:
    gz_path, xml_path = _paths()
    if xml_path.exists() and xml_path.stat().st_size > 0:
        log.info("using cached %s", xml_path)
        return xml_path
    xml_path.unlink(missing_ok=True)
    if not gz_path.exists():
        url = mesh_xml_url()
        log.info("downloading %s", url)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with gz_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    if not _is_gzip(gz_path):
        # server may serve plain XML despite the .gz filename
        log.info("%s is plain XML; renaming to %s", gz_path, xml_path)
        gz_path.rename(xml_path)
        return xml_path
    log.info("decompressing to %s", xml_path)
    with gzip.open(gz_path, "rb") as src, xml_path.open("wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)
    return xml_path


def parse_descriptors(xml_path: Path, limit: int | None = None):
    """Yield {ui, name, tree_numbers, terms} per DescriptorRecord (streaming)."""
    count = 0
    for _, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag != "DescriptorRecord":
            continue
        ui = elem.findtext("DescriptorUI")
        name = elem.findtext("DescriptorName/String")
        tree_numbers = [t.text for t in elem.findall("TreeNumberList/TreeNumber") if t.text]
        terms: set[str] = set()
        for term in elem.iter("Term"):
            t = term.findtext("String")
            if t:
                terms.add(t)
        terms.discard(name)
        yield {"ui": ui, "name": name, "tree_numbers": tree_numbers, "terms": sorted(terms)}
        elem.clear()
        count += 1
        if limit and count >= limit:
            return
        if count % 5000 == 0:
            log.info("parsed %d descriptors", count)


def parent_tree_number(tn: str) -> str | None:
    """C18.452.394.750 -> C18.452.394 ; C18 -> None."""
    parts = tn.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def load_into_neo4j(records: list[dict]) -> None:
    # 1. Descriptor nodes
    rows = [{"ui": r["ui"], "name": r["name"], "treeNumbers": r["tree_numbers"]} for r in records]
    for i in range(0, len(rows), BATCH):
        run_write(
            """
            UNWIND $rows AS row
            MERGE (d:MeshDescriptor {ui: row.ui})
            SET d.name = row.name, d.treeNumbers = row.treeNumbers
            """,
            rows=rows[i : i + BATCH],
        )
        log.info("loaded descriptors %d/%d", min(i + BATCH, len(rows)), len(rows))

    # 2. Tree-number -> descriptor lookup for parent edges
    tn_to_ui = {tn: r["ui"] for r in records for tn in r["tree_numbers"]}
    edges = []
    for r in records:
        for tn in r["tree_numbers"]:
            parent_tn = parent_tree_number(tn)
            if parent_tn and parent_tn in tn_to_ui:
                edges.append({"child": r["ui"], "parent": tn_to_ui[parent_tn]})
    edges = [dict(t) for t in {tuple(sorted(e.items())) for e in edges}]
    for i in range(0, len(edges), BATCH):
        run_write(
            """
            UNWIND $rows AS row
            MATCH (child:MeshDescriptor {ui: row.child})
            MATCH (parent:MeshDescriptor {ui: row.parent})
            MERGE (parent)-[:BROADER_THAN]->(child)
            """,
            rows=edges[i : i + BATCH],
        )
    log.info("created %d BROADER_THAN edges", len(edges))

    # 3. Entry terms (synonyms)
    term_rows = [
        {"descriptorUi": r["ui"], "term": t}
        for r in records for t in r["terms"]
    ]
    for i in range(0, len(term_rows), BATCH):
        run_write(
            """
            UNWIND $rows AS row
            MERGE (t:MeshTerm {name: row.term, descriptorUi: row.descriptorUi})
            WITH t, row
            MATCH (d:MeshDescriptor {ui: row.descriptorUi})
            MERGE (t)-[:SYNONYM_OF]->(d)
            """,
            rows=term_rows[i : i + BATCH],
        )
        if (i // BATCH) % 10 == 0:
            log.info("loaded terms %d/%d", min(i + BATCH, len(term_rows)), len(term_rows))

    stats = run(
        """
        MATCH (d:MeshDescriptor) WITH count(d) AS descriptors
        MATCH (t:MeshTerm) WITH descriptors, count(t) AS terms
        MATCH ()-[r:BROADER_THAN]->() RETURN descriptors, terms, count(r) AS edges
        """
    )[0]
    log.info("MeSH in Neo4j: %s", stats)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="load only first N descriptors")
    args = ap.parse_args()
    xml_path = download()
    records = list(parse_descriptors(xml_path, args.limit))
    log.info("parsed %d descriptor records", len(records))
    load_into_neo4j(records)


if __name__ == "__main__":
    main()
