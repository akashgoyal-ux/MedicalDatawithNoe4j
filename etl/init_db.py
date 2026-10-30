"""Apply schema.cypher (constraints + full-text indexes) to the database."""
import re
from pathlib import Path

from etl.neo4j_client import run_write


def main() -> None:
    cypher = (Path(__file__).parent / "schema.cypher").read_text()
    cypher = "\n".join(l for l in cypher.splitlines() if not l.strip().startswith("//"))
    statements = [s.strip() for s in re.split(r";\s*(?:\n|$)", cypher) if s.strip()]
    for stmt in statements:
        run_write(stmt)
        print("ok:", stmt.splitlines()[0], "...")
    print(f"applied {len(statements)} statements")


if __name__ == "__main__":
    main()
