"""Repeated user input is not evidence of a mirrored transcript occurrence."""
import sqlite3
from types import SimpleNamespace

import pytest

from api import models


@pytest.mark.parametrize("content", ["try again", [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 210_000}}]])
@pytest.mark.parametrize("later_metadata", [
    {"timestamp": 2000.0},
    {"timestamp": 1000.1},
    {},
    {"timestamp": "invalid"},
    {"timestamp": 1000.0, "id": "other"},
    {"timestamp": 1000.0, "message_id": "other"},
    {"timestamp": 1000.0, "_state_db_row_id": 2},
])
def test_restore_and_context_preserve_distinct_user_occurrences(content, later_metadata):
    first = {"role": "user", "content": content, "timestamp": 1000.0}
    later = {"role": "user", "content": content, **later_metadata}
    sidecar = [first, {"role": "assistant", "content": "first answer", "timestamp": 1001.0}]
    session = SimpleNamespace(messages=sidecar, context_messages=sidecar)

    for prefer_context in (False, True):
        result = models.reconciled_state_db_messages_for_session(
            session, prefer_context=prefer_context, state_messages=[later],
        )
        assert [row for row in result if row["role"] == "user"] == [first, later]


@pytest.mark.parametrize("timestamp", [None, "invalid", "", float("nan"), float("inf")])
def test_unknown_occurrences_are_not_duplicates(timestamp):
    first = {"role": "user", "content": "again", "timestamp": timestamp}
    second = dict(first)
    assert models.merge_session_messages_append_only([first], [second]) == [first, second]


def test_exact_mirrored_occurrence_is_deduplicated():
    first = {"role": "user", "content": "again", "timestamp": 1000.0, "id": "a", "message_id": "b"}
    assert models.merge_session_messages_append_only([first], [dict(first)]) == [first]


@pytest.mark.parametrize("identity", ["id", "message_id", "_state_db_row_id"])
def test_every_private_identity_must_agree(identity):
    first = {"role": "user", "content": "again", "timestamp": 1000.0,
             "id": "a", "message_id": "b", "_state_db_row_id": 1}
    second = dict(first, **{identity: "different"})
    assert models.merge_session_messages_append_only([first], [second]) == [first, second]


def test_context_alignment_preserves_unmatched_user_between_mirrors():
    sidecar = [
        {"role": "user", "content": "again", "timestamp": 1000.0},
        {"role": "assistant", "content": "answer", "timestamp": 1001.0},
        {"role": "assistant", "content": "tail", "timestamp": 1003.0},
    ]
    repeated = dict(sidecar[0], timestamp=1002.0)
    state = sidecar[:2] + [repeated, sidecar[2]]
    assert models.state_db_delta_after_context(sidecar, state) == state[2:]


def test_context_alignment_does_not_consume_a_later_repeated_exchange():
    sidecar = [
        {"role": "user", "content": "again", "timestamp": 1000.0},
        {"role": "assistant", "content": "answer", "timestamp": 1001.0},
    ]
    later = [dict(row, timestamp=row["timestamp"] + 1000) for row in sidecar]
    assert models.state_db_delta_after_context(sidecar, later) == later


def test_bounded_prefix_keys_match_full_reader_occurrences(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL, tool_calls TEXT)")
        conn.executemany("INSERT INTO messages VALUES (?, 's', 'user', 'again', ?, NULL)", [(1, 1000.0), (2, 2000.0)])
    monkeypatch.setattr(models, "_active_state_db_path", lambda: db)
    rows = models.get_state_db_session_messages("s")
    keys = models.get_state_db_session_message_keys_before_timestamp("s", 3000)
    assert keys == [models._session_message_visible_key(row) for row in rows]
    assert keys[0] != keys[1]
