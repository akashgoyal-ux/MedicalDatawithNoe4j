"""Ontology registry: loads ontology/medical_graph.ttl with rdflib and exposes
the graph schema (node classes, key properties, relationship types with
domain/range) as Python objects.

The TTL file is the single source of truth. Loaders and services reference
`SCHEMA` instead of hardcoding label/relationship strings, and
tests/test_ontology.py fails if code uses a label or edge type that isn't
declared in the ontology.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDFS, RDF

MG = Namespace("https://example.org/medical-graph#")
OWL_THING = URIRef("http://www.w3.org/2002/07/owl#")

ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology" / "medical_graph.ttl"


@dataclass(frozen=True)
class NodeClass:
    name: str           # Neo4j label
    key_property: str   # uniqueness key (may be composite, comma-separated)
    comment: str = ""


@dataclass(frozen=True)
class EdgeType:
    name: str           # Neo4j relationship type
    domain: str         # source label
    range: str          # target label
    comment: str = ""


@dataclass(frozen=True)
class GraphSchema:
    nodes: dict[str, NodeClass] = field(default_factory=dict)
    edges: dict[str, EdgeType] = field(default_factory=dict)

    def label(self, name: str) -> str:
        if name not in self.nodes:
            raise KeyError(f"node class {name!r} not declared in ontology")
        return name

    def rel(self, name: str) -> str:
        if name not in self.edges:
            raise KeyError(f"relationship type {name!r} not declared in ontology")
        return name

    def constraint_cypher(self, name: str) -> str:
        """Generate the uniqueness-constraint statement for a node class."""
        cls = self.nodes[name]
        props = [p.strip() for p in cls.key_property.split(",")]
        if len(props) == 1:
            req = f"n.{props[0]} IS UNIQUE"
        else:
            req = f"({', '.join('n.' + p for p in props)}) IS UNIQUE"
        safe = name.lower()
        return (f"CREATE CONSTRAINT {safe}_key IF NOT EXISTS "
                f"FOR (n:{name}) REQUIRE {req}")


@lru_cache(maxsize=1)
def load_schema(path: Path = ONTOLOGY_PATH) -> GraphSchema:
    g = Graph()
    g.parse(path, format="turtle")
    nodes: dict[str, NodeClass] = {}
    edges: dict[str, EdgeType] = {}

    for s in g.subjects(RDF.type, RDFS.Class):
        name = str(s).removeprefix(str(MG))
        key = g.value(s, MG.keyProperty)
        comment = g.value(s, RDFS.comment)
        nodes[name] = NodeClass(name=name, key_property=str(key) if key else "",
                                comment=str(comment) if comment else "")

    for s in g.subjects(RDF.type, URIRef("http://www.w3.org/2002/07/owl#ObjectProperty")):
        name = str(s).removeprefix(str(MG))
        domain = g.value(s, RDFS.domain)
        rng = g.value(s, RDFS.range)
        comment = g.value(s, RDFS.comment)
        edges[name] = EdgeType(
            name=name,
            domain=str(domain).removeprefix(str(MG)) if domain else "",
            range=str(rng).removeprefix(str(MG)) if rng else "",
            comment=str(comment) if comment else "",
        )
    return GraphSchema(nodes=nodes, edges=edges)


SCHEMA = load_schema()
