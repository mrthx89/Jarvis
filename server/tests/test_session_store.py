"""Read-only Hermes session binding and answer observation contracts."""

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from nexo_server.session_store import resolve_pinned_session


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "hermes state?#.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, title TEXT, source TEXT,
                parent_session_id TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                role TEXT, content TEXT, finish_reason TEXT,
                tool_calls TEXT, active INTEGER DEFAULT 1,
                compacted INTEGER DEFAULT 0, display_kind TEXT
            );
        """)
    return path


def session(path, sid="canonical", title="nexo", source="local", parent=None):
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                   (sid, title, source, parent))


def test_resolves_only_local_root_and_persists_private_binding(db_path, tmp_path):
    session(db_path)
    session(db_path, "remote", source="telegram")
    session(db_path, "child", parent="canonical")
    state = tmp_path / "binding" / "session.json"
    before = db_path.read_bytes()

    assert resolve_pinned_session(db_path, state) == "canonical"

    assert json.loads(state.read_text()) == {
        "session_id": "canonical", "db_path": str(db_path.resolve())}
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700
    assert db_path.read_bytes() == before


def test_persisted_id_survives_rename_and_new_nexo_session(db_path, tmp_path):
    session(db_path)
    state = tmp_path / "binding" / "session.json"
    resolve_pinned_session(db_path, state)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE sessions SET title = 'renamed' WHERE id = 'canonical'")
    session(db_path, "replacement")
    before = state.read_bytes()

    assert resolve_pinned_session(db_path, state) == "canonical"
    assert state.read_bytes() == before


def message(path, text="answer", sid="canonical", role="assistant", finish="stop",
            tools=None, active=1, compacted=0):
    with sqlite3.connect(path) as db:
        return db.execute(
            "INSERT INTO messages (session_id, content, role, finish_reason, "
            "tool_calls, active, compacted) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, text, role, finish, tools, active, compacted)).lastrowid


@pytest.mark.parametrize("changes", [
    {"role": "user"}, {"role": "tool"}, {"finish": "tool_calls"},
    {"finish": None}, {"tools": '[{"function": {}}]'},
    {"active": 0}, {"compacted": 1}, {"text": "  "},
    {"text": "x" * 32001}, {"text": None}, {"sid": "other"},
])
def test_reader_never_returns_nonfinal_or_foreign_messages(db_path, changes):
    from nexo_server.session_store import HermesAnswerReader

    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    message(db_path, **changes)
    assert reader.read_new() == []
    assert reader.read_new() == []


def test_reader_filters_and_advances_with_bounded_batches(db_path):
    from nexo_server.session_store import HermesAnswerReader

    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    for _ in range(101):
        last = message(db_path, role="tool")
    assert reader.read_new() == []
    assert reader.cursor < last
    assert reader.read_new() == []
    assert reader.cursor == last
    for tools in (None, "", "[]"):
        last = message(db_path, tools=tools, finish="end_turn")
    assert len(reader.read_new()) == 3
    assert reader.cursor == last
    last = message(db_path)
    reader.skip_current()
    assert reader.cursor == last
    assert reader.read_new() == []


@pytest.mark.parametrize("kind", ["hidden", "failed_turn", "async_delegation_complete"])
def test_reader_never_speaks_display_only_or_hidden_finals(db_path, kind):
    from nexo_server.session_store import HermesAnswerReader

    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    mid = message(db_path, text="Not a user-facing final")
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE messages SET display_kind = ? WHERE id = ?", (kind, mid))
    assert reader.read_new() == []
    assert reader.cursor == mid


def test_reader_initializes_at_current_global_max_without_replay(db_path):
    from nexo_server.session_store import HermesAnswerReader

    message(db_path)
    latest = message(db_path, sid="other")
    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    assert reader.cursor == latest
    assert reader.read_new() == []
    new_id = message(db_path, text="fresh answer")
    assert reader.read_new() == [{"id": new_id, "text": "fresh answer"}]
    assert reader.cursor == new_id
    assert reader.read_new() == []


@pytest.mark.parametrize("count", [0, 2])
def test_absent_or_ambiguous_discovery_never_persists(db_path, tmp_path, count):
    for number in range(count):
        session(db_path, str(number))
    state = tmp_path / "binding" / "session.json"
    with pytest.raises(ValueError):
        resolve_pinned_session(db_path, state)
    assert not state.exists()


@pytest.mark.parametrize("title,source,parent", [
    ("Nexo", "local", None), ("renamed", "local", None),
    ("nexo", "telegram", None), ("nexo", "local", "parent")])
def test_explicit_invalid_session_is_rejected(db_path, tmp_path, title, source, parent):
    session(db_path, "chosen", title, source, parent)
    session(db_path, "valid")
    state = tmp_path / "binding" / "session.json"
    with pytest.raises(ValueError):
        resolve_pinned_session(db_path, state, "chosen")
    assert not state.exists()


def test_missing_db_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    state = tmp_path / "binding" / "session.json"
    with pytest.raises(sqlite3.OperationalError):
        resolve_pinned_session(path, state)
    assert not path.exists()
    assert not state.exists()


@pytest.mark.parametrize("binding", ["null", "[]", "{}", '{"session_id": 4}',
                                    '{"session_id": "", "db_path": "x"}', "broken"])
def test_invalid_binding_fails_closed(db_path, tmp_path, binding):
    session(db_path)
    state = tmp_path / "session.json"
    state.write_text(binding)
    with pytest.raises(ValueError):
        resolve_pinned_session(db_path, state)
    assert state.read_text() == binding


def test_explicit_id_disambiguates_exact_title_and_local_root(db_path, tmp_path):
    sid = "chosen' OR 1=1 --"
    session(db_path, sid)
    session(db_path, "other")
    state = tmp_path / "binding" / "session.json"
    assert resolve_pinned_session(db_path, state, sid) == sid
    assert resolve_pinned_session(db_path, state, sid) == sid


@pytest.mark.parametrize("change", ["delete", "remote", "db_switch", "conflict"])
def test_existing_binding_fails_closed_without_relink(db_path, tmp_path, change):
    session(db_path)
    state = tmp_path / "binding" / "session.json"
    resolve_pinned_session(db_path, state)
    session(db_path, "replacement")
    before = state.read_bytes()
    requested = ""
    if change == "delete":
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE id = 'canonical'")
    elif change == "remote":
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE sessions SET source = 'telegram' WHERE id = 'canonical'")
    elif change == "db_switch":
        other = tmp_path / "other.db"
        other.write_bytes(db_path.read_bytes())
        db_path = other
    else:
        requested = "replacement"

    with pytest.raises(ValueError):
        resolve_pinned_session(db_path, state, requested)
    assert state.read_bytes() == before
