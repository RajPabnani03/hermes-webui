"""Regression tests for #7902: streaming reconnect `_row_id` amplification.

A reconnect while an assistant row is still streaming can persist multiple
snapshots of the same durable `_row_id` with divergent `api_content`. The
pre-merge collapse in `api/models.py` must reduce those snapshots to the most
advanced one so repeated merges stay at exactly one copy.
"""


def _partial_assistant(api_text, first_token_ms, row_id=24318):
    return {
        "role": "assistant",
        "content": "",
        "timestamp": 1000.0,
        "finish_reason": "incomplete",
        "api_content": api_text,
        "_row_id": row_id,
        "_firstTokenMs": first_token_ms,
        "_db_persisted": True,
    }


def _settled_assistant(api_text="visible reply", row_id=24318):
    return {
        "role": "assistant",
        "content": "visible reply",
        "timestamp": 1000.0,
        "finish_reason": "stop",
        "api_content": api_text,
        "_row_id": row_id,
        "_firstTokenMs": 10,
        "_db_persisted": True,
    }


def _user_row(content="canonical prompt", row_id=24317):
    return {
        "role": "user",
        "content": content,
        "timestamp": 999.0,
        "_row_id": row_id,
        "_db_persisted": True,
    }


def _row_ids(merged):
    return [m.get("_row_id") for m in merged if isinstance(m, dict)]


def test_single_snapshot_fast_path_untouched():
    import api.models as models

    sidecar = [_user_row(), _partial_assistant("Reas: partial", 12)]
    state = [_user_row(), _partial_assistant("Reas: partial longer", 14)]
    merged = models.merge_session_messages_append_only(sidecar, state)
    assert _row_ids(merged).count(24318) == 1


def test_divergent_snapshots_collapse_to_most_advanced():
    import api.models as models

    merged = [_user_row(), _partial_assistant("Reas:", 10), _partial_assistant("Reas: partial", 12)]
    state = [_user_row(), _partial_assistant("Reas: partial longer", 14)]
    merged = models.merge_session_messages_append_only(merged, state)
    rows = _row_ids(merged)
    assert rows.count(24318) == 1, f"row 24318 present {rows.count(24318)}x"
    kept = next(m for m in merged if m.get("_row_id") == 24318)
    assert kept["api_content"] == "Reas: partial longer"


def test_repeated_merges_stay_stable():
    import api.models as models

    merged = [_user_row(), _partial_assistant("Reas:", 10), _partial_assistant("Reas: partial", 12)]
    state = [_user_row(), _partial_assistant("Reas: partial longer", 14)]
    for _ in range(5):
        merged = models.merge_session_messages_append_only(merged, state)
    rows = _row_ids(merged)
    assert rows.count(24318) == 1, f"row 24318 grew to {rows.count(24318)} copies"


def test_settled_rows_never_collapsed():
    import api.models as models

    sidecar = [_user_row(), _settled_assistant("payload-a"), _settled_assistant("payload-b")]
    state = [_user_row(), _settled_assistant("payload-c")]
    out_sidecar, out_state = models._collapse_streaming_row_id_snapshots(sidecar, state)
    # Settled buckets are returned untouched: same length, same payloads.
    assert len(out_sidecar) == 3 and len(out_state) == 2
    assert out_sidecar[1]["api_content"] == "payload-a"
    assert out_sidecar[2]["api_content"] == "payload-b"
    assert out_state[1]["api_content"] == "payload-c"


def test_mixed_skeleton_and_settled_bucket_untouched():
    import api.models as models

    sidecar = [_user_row(), _partial_assistant("Reas: partial", 12)]
    state = [_user_row(), _settled_assistant("done")]
    out_sidecar, out_state = models._collapse_streaming_row_id_snapshots(sidecar, state)
    # Mixed bucket (skeleton + settled sharing one _row_id) stays untouched.
    assert len(out_sidecar) == 2 and len(out_state) == 2
    assert out_sidecar[1]["api_content"] == "Reas: partial"
    assert out_state[1]["api_content"] == "done"


def test_rows_without_row_id_untouched():
    import api.models as models

    def _no_id(api_text):
        return {
            "role": "assistant",
            "content": "",
            "timestamp": 1000.0,
            "finish_reason": "incomplete",
            "api_content": api_text,
        }

    sidecar = [_user_row(), _no_id("Reas:"), _no_id("Reas: partial")]
    state = [_user_row(), _no_id("Reas: partial longer")]
    merged = models.merge_session_messages_append_only(sidecar, state)
    assert len(merged) == len(sidecar)


def test_tool_call_snapshots_never_collapsed():
    import api.models as models

    def _tool_snapshot(api_text, ms):
        msg = _partial_assistant(api_text, ms)
        msg["tool_calls"] = [{"id": "call-1", "name": "search"}]
        return msg

    sidecar = [_user_row(), _tool_snapshot("Reas:", 10), _tool_snapshot("Reas: partial", 12)]
    state = [_user_row(), _tool_snapshot("Reas: partial longer", 14)]
    merged = models.merge_session_messages_append_only(sidecar, state)
    assert _row_ids(merged).count(24318) >= 2
