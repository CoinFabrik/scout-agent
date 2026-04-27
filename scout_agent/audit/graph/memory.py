import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_savers: dict[str, SqliteSaver] = {}
_conns: dict[str, sqlite3.Connection] = {}

# Allow our custom domain types to be deserialized from checkpoints
_ALLOWED_MODULES = [
    ("scout_agent.domain.audit", "FileAuditResponse"),
    ("scout_agent.domain.audit", "Finding"),
    ("scout_agent.domain.audit", "ExpertResult"),
]


def get_sqlite_saver(
    db_path: str | Path = ".scout-ai/scout_audit_memory.sqlite",
) -> SqliteSaver:
    """
    Returns a LangGraph SqliteSaver checkpointer for the given *db_path*.

    Each unique resolved path gets its own connection and saver instance
    so that ``memory.sqlite`` (main graph) and ``.audit_memory.sqlite``
    (per-file agents) are kept separate.
    """
    resolved_path = Path(db_path).resolve()
    resolved_key = str(resolved_path)

    if resolved_key not in _savers:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(resolved_key, check_same_thread=False)
        serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODULES)
        saver = SqliteSaver(conn, serde=serde)
        saver.setup()
        _conns[resolved_key] = conn
        _savers[resolved_key] = saver

    return _savers[resolved_key]
