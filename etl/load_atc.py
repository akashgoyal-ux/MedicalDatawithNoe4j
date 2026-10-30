"""Load the WHO ATC classification hierarchy into Neo4j.

Source: WHOCC ATC/DDD Index (https://atcddd.fhi.no/atc_ddd_index/).
Pages are fetched politely (cached on disk, delay between requests).

ATC structure: L1 (1 letter) -> L2 (2 digits) -> L3 (1 letter)
-> L4 (1 letter) -> L5 (2 digits). Example: C03XA01 = tolvaptan.

Usage:
    python -m etl.load_atc --groups C A          # subset (prototype)
    python -m etl.load_atc                        # all 14 groups (slow, ~1500 pages)
"""
import argparse
import hashlib
import logging
import re
import time
from pathlib import Path

import requests

from etl.config import atc_index_url
from etl.neo4j_client import run, run_write

log = logging.getLogger("load_atc")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

CACHE_DIR = Path("data/atc_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "medicaldata-neo4j-prototype/0.1 (educational R&D use)"}
REQUEST_DELAY_S = 0.4

LEVEL_LABELS = {1: "ATCLevel1", 2: "ATCLevel2", 3: "ATCLevel3", 4: "ATCLevel4", 5: "ATCLevel5"}


def fetch_index_page(code: str = "") -> str:
    """Fetch one ATC index page, with disk cache."""
    key = hashlib.md5(code.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.html"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    resp = requests.get(atc_index_url(), params={"code": code} if code else {},
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    cache_file.write_text(resp.text, encoding="utf-8")
    time.sleep(REQUEST_DELAY_S)
    return resp.text


# Rows in the ATC index tables look like:  C03XA01  tolvaptan  (plus DDD columns on L5)
CODE_RE = re.compile(r"^([A-V])(\d{2})?([A-Z])?([A-Z])?(\d{2})?$")


LINK_RE = re.compile(r'href="[^"]*?code=([A-V][0-9A-Z]*)&[^"]*"[^>]*>([^<]+)<')


def parse_children(html: str, parent_code: str) -> list[tuple[str, str]]:
    """Extract (code, name) pairs exactly one level below parent_code.

    ATC index pages link to children as ?code=<code>; ancestor breadcrumb
    links are excluded by requiring exactly parent_level + 1.
    """
    parent_level = code_level(parent_code)
    out: list[tuple[str, str]] = []
    for code, name in LINK_RE.findall(html):
        code, name = code.strip(), name.strip()
        if code_level(code) == parent_level + 1 and code.startswith(parent_code):
            out.append((code, name))
    seen, uniq = set(), []
    for c, n in out:
        if c not in seen:
            seen.add(c)
            uniq.append((c, n))
    return uniq


def code_level(code: str) -> int:
    if code == "":
        return 0
    m = CODE_RE.match(code)
    if not m:
        raise ValueError(f"not an ATC code: {code!r}")
    return sum(1 for g in m.groups() if g)


def crawl(groups: list[str]) -> dict[str, dict]:
    """Breadth-first crawl of the ATC index; returns {code: {name, level, parent}}."""
    nodes: dict[str, dict] = {}
    root_html = fetch_index_page("")
    for code, name in parse_children(root_html, ""):
        if code_level(code) != 1:
            continue
        if groups and code not in groups:
            continue
        nodes[code] = {"name": name, "level": 1, "parent": None}
        log.info("L1 %s %s", code, name)

    frontier = [c for c, d in nodes.items()]
    while frontier:
        next_frontier: list[str] = []
        for parent in frontier:
            html = fetch_index_page(parent)
            for code, name in parse_children(html, parent):
                if code in nodes:
                    continue
                level = code_level(code)
                nodes[code] = {"name": name, "level": level, "parent": parent}
                if level < 5:
                    next_frontier.append(code)
            log.info("crawled %s (%d children, %d total)", parent,
                     sum(1 for d in nodes.values() if d["parent"] == parent), len(nodes))
        frontier = next_frontier
    return nodes


def load_into_neo4j(nodes: dict[str, dict]) -> None:
    rows = [
        {"code": code, "name": d["name"], "level": d["level"], "parent": d["parent"]}
        for code, d in nodes.items()
    ]
    for level in range(1, 6):
        label = LEVEL_LABELS[level]
        run_write(
            f"""
            UNWIND $rows AS row
            MERGE (n:{label} {{code: row.code}})
            SET n.name = row.name, n.level = row.level
            """,
            rows=[r for r in rows if r["level"] == level],
        )
    for level in range(2, 6):
        parent_label, child_label = LEVEL_LABELS[level - 1], LEVEL_LABELS[level]
        rel = "HAS_SUBSTANCE" if level == 5 else "HAS_SUBGROUP"
        run_write(
            f"""
            UNWIND $rows AS row
            MATCH (p:{parent_label} {{code: row.parent}})
            MATCH (c:{child_label} {{code: row.code}})
            MERGE (p)-[:{rel}]->(c)
            """,
            rows=[r for r in rows if r["level"] == level],
        )
    count = run("MATCH (n) WHERE n:ATCLevel1 OR n:ATCLevel2 OR n:ATCLevel3 OR n:ATCLevel4 OR n:ATCLevel5 "
                "RETURN count(n) AS c")[0]["c"]
    log.info("ATC nodes in Neo4j: %d", count)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=None,
                    help="L1 group letters to load (default: all 14)")
    args = ap.parse_args()
    nodes = crawl(args.groups)
    log.info("crawled %d ATC codes", len(nodes))
    load_into_neo4j(nodes)


if __name__ == "__main__":
    main()
