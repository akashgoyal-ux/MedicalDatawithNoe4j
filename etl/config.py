"""Central configuration. All connection details and external URLs come from .env."""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required setting {name} — add it to .env (see .env.example)")
    return value


def neo4j_settings() -> dict[str, str]:
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", ""),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
    }


def atc_index_url() -> str:
    """WHOCC ATC/DDD index base URL."""
    return _required("ATC_INDEX_URL")


def mesh_xml_url() -> str:
    """NLM MeSH descriptor XML (gzipped) download URL."""
    return _required("MESH_XML_URL")


def openfda_label_url() -> str:
    """openFDA drug label endpoint."""
    return _required("OPENFDA_LABEL_URL")


def dailymed_drug_url(set_id: str) -> str:
    """DailyMed drug-info URL template; must contain {set_id}."""
    return _required("DAILYMED_DRUG_URL_TEMPLATE").format(set_id=set_id)


def neo4j_browser_url() -> str:
    """Neo4j Browser base URL (used for cmd=query deep links)."""
    return _required("NEO4J_BROWSER_URL")
