"""Offline unit tests for ETL parsing logic (no Neo4j required)."""
from etl.load_atc import code_level, parse_children
from etl.load_mesh import parent_tree_number
from etl.ingest_openfda import normalize, tag_document
import ahocorasick


def test_code_level():
    assert code_level("C") == 1
    assert code_level("C03") == 2
    assert code_level("C03X") == 3
    assert code_level("C03XA") == 4
    assert code_level("C03XA01") == 5


def test_parent_tree_number():
    assert parent_tree_number("C18.452.394.750") == "C18.452.394"
    assert parent_tree_number("C19.246") == "C19"
    assert parent_tree_number("C18") is None


def test_parse_children_from_fixture():
    html = """
    <a href="./?code=C&amp;showdescription=no">CARDIOVASCULAR SYSTEM</a>
    <a href="./?code=C03&amp;showdescription=no">DIURETICS</a>
    <a href="./?code=C03X&amp;showdescription=no">OTHER DIURETICS</a>
    <a href="./?code=C03XA&amp;showdescription=no">Vasopressin antagonists</a>
    <a href="./?code=C03XA01&amp;showdescription=yes">tolvaptan</a>
    """
    # one level below C03X: only C03XA (breadcrumb and deeper links excluded)
    children = parse_children(html, "C03X")
    assert children == [("C03XA", "Vasopressin antagonists")]
    root_children = parse_children(html, "")
    assert ("C", "CARDIOVASCULAR SYSTEM") in root_children


def test_normalize():
    assert normalize("Diabetes Mellitus, Type 2!") == "diabetes mellitus type 2"


def test_tag_document_whole_phrase():
    automaton = ahocorasick.Automaton()
    automaton.add_word("diabetes mellitus", ("diabetes mellitus", "D003920"))
    automaton.add_word("diabetic foot", ("diabetic foot", "D017719"))
    automaton.make_automaton()
    hits = tag_document(automaton,
                        "Indicated for Diabetes Mellitus and prevention of diabetic foot ulcers.")
    assert hits == ["D003920", "D017719"]
