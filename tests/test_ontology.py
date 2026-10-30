"""Ontology governance: every Neo4j label / relationship type used anywhere in
the codebase must be declared in ontology/medical_graph.ttl."""
import re
from pathlib import Path

from etl.ontology import SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SOURCE_GLOBS = ("etl/**/*.py", "api/**/*.py", "app/**/*.py", "etl/**/*.cypher")

LABEL_RE = re.compile(r"\(\w*:(ATCLevel\d|[A-Z][A-Za-z0-9]+)")
REL_RE = re.compile(r"\[[:a-zA-Z_]*:([A-Z][A-Z0-9_]+)")


def _source_text() -> str:
    chunks = []
    for pattern in SOURCE_GLOBS:
        for path in ROOT.glob(pattern):
            chunks.append(path.read_text())
    return "\n".join(chunks)


def test_ontology_loads_and_covers_core_classes():
    for expected in ["Drug", "MeshDescriptor", "MeshTerm", "RegulatoryDocument",
                     "ATCLevel1", "ATCLevel5", "Trial", "Sponsor",
                     "AdverseEventReport", "Reaction", "ICD10Code", "DrugAlias"]:
        assert SCHEMA.label(expected)
    for expected in ["HAS_SUBGROUP", "HAS_SUBSTANCE", "CLASSIFIES", "HAS_ATC_CODE",
                     "BROADER_THAN", "SYNONYM_OF", "INDICATED_FOR", "MENTIONS",
                     "ABOUT", "ISSUED_BY", "STUDIES", "TESTS", "SPONSORED_BY",
                     "HAS_REPORT", "REPORTS_REACTION", "MAPS_TO_MESH",
                     "ALIAS_OF", "ALIGNS_WITH", "SIMILAR_TO"]:
        assert SCHEMA.rel(expected)


def test_no_undeclared_labels_in_code():
    text = _source_text()
    undeclared = {m for m in LABEL_RE.findall(text) if m not in SCHEMA.nodes}
    assert not undeclared, f"labels used but not in ontology: {undeclared}"


def test_no_undeclared_relationships_in_code():
    text = _source_text()
    undeclared = {m for m in REL_RE.findall(text) if m not in SCHEMA.edges}
    assert not undeclared, f"relationships used but not in ontology: {undeclared}"


def test_edge_endpoints_reference_declared_classes():
    for edge in SCHEMA.edges.values():
        assert edge.domain in SCHEMA.nodes, f"{edge.name} domain {edge.domain!r}"
        assert edge.range in SCHEMA.nodes, f"{edge.name} range {edge.range!r}"
