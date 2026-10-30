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


def test_normalize_product_name():
    from etl.ingest_faers import normalize_product_name
    assert normalize_product_name("DURAGESIC-100") == "duragesic"
    assert normalize_product_name("GLIPIZIDE XL") == "glipizide"
    assert normalize_product_name("IBUPROFEN 200 MG") == "ibuprofen"
    assert normalize_product_name("METOPROLOL TARTRATE") == "metoprolol tartrate"


def test_extract_suspect_names_prefers_suspect_and_openfda():
    from etl.ingest_faers import extract_suspect_names
    report = {"patient": {"drug": [
        {"drugcharacterization": "2", "medicinalproduct": "ASPIRIN"},
        {"drugcharacterization": "1", "medicinalproduct": "GLIPIZIDE XL",
         "openfda": {"generic_name": ["GLIPIZIDE"]}},
    ]}}
    assert extract_suspect_names(report) == ["GLIPIZIDE"]


def test_extract_suspect_names_falls_back_to_normalized_product():
    from etl.ingest_faers import extract_suspect_names
    report = {"patient": {"drug": [{"drugcharacterization": "1",
                                    "medicinalproduct": "DURAGESIC-100"}]}}
    assert extract_suspect_names(report) == ["duragesic"]


def test_extract_reactions_dedupes_and_titles():
    from etl.ingest_faers import extract_reactions
    report = {"patient": {"reaction": [
        {"reactionmeddrapt": "OVERDOSE"},
        {"reactionmeddrapt": "overdose"},
        {"reactionmeddrapt": "NAUSEA"},
        {},
    ]}}
    assert extract_reactions(report) == ["Nausea", "Overdose"]


def test_iso_date():
    from etl.ingest_faers import iso_date
    assert iso_date("20080707") == "2008-07-07"
    assert iso_date(None) is None
    assert iso_date("2008") is None


def test_parse_trial_extracts_modules():
    from etl.ingest_trials import parse_trial
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT001", "briefTitle": "Trial of X"},
            "statusModule": {"overallStatus": "RECRUITING",
                             "startDateStruct": {"date": "2025-01"}},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma"}},
            "designModule": {"phases": ["PHASE3"]},
            "armsInterventionsModule": {"interventions": [
                {"type": "DRUG", "name": "Metformin"},
                {"type": "OTHER", "name": "Placebo"},
            ]},
        },
        "derivedSection": {"conditionBrowseModule": {"meshes": [
            {"id": "D003920", "term": "Diabetes Mellitus"}]}},
    }
    t = parse_trial(study)
    assert t["nctId"] == "NCT001"
    assert t["phase"] == "PHASE3"
    assert t["sponsor"] == "Acme Pharma"
    assert t["condition_mesh_uis"] == ["D003920"]
    assert t["interventions"] == ["Metformin"]  # non-drug intervention excluded
