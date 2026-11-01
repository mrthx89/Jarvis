"""Read-only access to Hermes sessions and completed assistant messages.

Only the private JSON binding file is writable. Hermes owns the SQLite database.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def _connect(db_path: Path) -> sqlite3.Connection:
    # as_uri escapes SQLite URI metacharacters in real filenames.
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True,
                           timeout=1.0)


def _persist_binding(state_path: Path, db_path: Path, session_id: str) -> None:
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"session_id": session_id, "db_path": str(db_path)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_pinned_session(db_path: Path, state_path: Path,
                           requested_id: str = "") -> str:
    """Bind to one local root named nexo without ever modifying Hermes state."""
    db_path = db_path.resolve()
    if state_path.exists():
        binding = json.loads(state_path.read_text(encoding="utf-8"))
        if (not isinstance(binding, dict)
                or not isinstance(binding.get("session_id"), str)
                or not binding["session_id"]
                or not isinstance(binding.get("db_path"), str)):
            raise ValueError("Invalid persisted session binding")
        if binding["db_path"] != str(db_path):
            raise ValueError("Pinned session belongs to a different database")
        session_id = binding["session_id"]
        if requested_id and requested_id != session_id:
            raise ValueError("Requested session conflicts with persisted binding")
        with closing(_connect(db_path)) as db:
            row = db.execute(
                "SELECT id FROM sessions WHERE id = ? AND source = ?",
                (session_id, "local"),
            ).fetchone()
        if row is None:
            raise ValueError("Pinned local session no longer exists")
        return session_id
    with closing(_connect(db_path)) as db:
        query = ("SELECT id FROM sessions WHERE title = ? AND source = ? "
                 "AND parent_session_id IS NULL")
        parameters = ("nexo", "local")
        if requested_id:
            query += " AND id = ?"
            parameters += (requested_id,)
        rows = db.execute(query + " LIMIT 2", parameters).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one local root nexo session")
    session_id = rows[0][0]
    _persist_binding(state_path, db_path, session_id)
    return session_id


class HermesAnswerReader:
    """Observe new messages using short-lived read-only connections."""

    def __init__(self, db_path: Path, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor

    def initialize(self) -> None:
        """Start at the current high-water mark; never replay history."""
        with closing(_connect(self.db_path)) as db:
            self._cursor = db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages"
            ).fetchone()[0]

    def high_water(self) -> int:
        """Snapshot a connection boundary without racing the worker's cursor."""
        with closing(_connect(self.db_path)) as db:
            return db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()[0]

    def skip_current(self) -> None:
        self._cursor = self.high_water()

    def last_matching_final(self, text: str, after: int, through: int) -> int | None:
        """Find one owned final across bounded pages, without retaining history."""
        before = through + 1
        with closing(_connect(self.db_path)) as db:
            while True:
                rows = db.execute(
                    "SELECT id, content FROM messages WHERE session_id = ? "
                    "AND id > ? AND id < ? AND role = 'assistant' "
                    "AND finish_reason IN ('stop', 'end_turn') "
                    "AND active = 1 AND compacted = 0 "
                    "AND COALESCE(display_kind, '') = '' "
                    "AND COALESCE(tool_calls, '') IN ('', '[]') "
                    "AND length(content) BETWEEN 1 AND 32000 "
                    "ORDER BY id DESC LIMIT 100",
                    (self.session_id, after, before),
                ).fetchall()
                if not rows:
                    return None
                for mid, content in rows:
                    if isinstance(content, str) and content.strip() == text.strip():
                        return mid
                before = rows[-1][0]

    def read_new(self) -> list[dict]:
        with closing(_connect(self.db_path)) as db:
            rows = db.execute(
                "SELECT id, CASE WHEN role = 'assistant' "
                "AND finish_reason IN ('stop', 'end_turn') "
                "AND active = 1 AND compacted = 0 "
                "AND COALESCE(display_kind, '') = '' "
                "AND COALESCE(tool_calls, '') IN ('', '[]') "
                "AND length(content) BETWEEN 1 AND 32000 "
                "THEN content ELSE NULL END "
                "FROM messages WHERE id > ? "
                "AND session_id = ? ORDER BY id LIMIT 100",
                (self._cursor, self.session_id),
            ).fetchall()
        if rows:
            self._cursor = rows[-1][0]
        return [{"id": row[0], "text": row[1]} for row in rows
                if isinstance(row[1], str) and row[1].strip()]
