"""The orchestrator must reach data only through gRPC: no sqlite3, no database paths."""
import re
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1] / "orchestrator"


def test_orchestrator_has_no_sqlite_or_db_paths():
    sources = list(ORCH.glob("*.py"))
    assert sources, "orchestrator sources not found"
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bsqlite3?\b", text, re.I), f"{path.name} mentions sqlite"
        assert not re.search(r"\.db\b", text), f"{path.name} mentions a .db path"
        assert not re.search(r"\bservices\.\w+\.db\b|from services", text), f"{path.name} imports service internals"
