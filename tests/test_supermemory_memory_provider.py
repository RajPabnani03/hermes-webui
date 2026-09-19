"""Focused tests for native Supermemory provider wiring (items 1, 5, 7).

Covers provider activation/config, profile mapping to ``hermes:{profile}``,
invalid names/sanitization-or-rejection, isolated-profile behavior,
isolated-profile tag replacement, and prevention of cross-profile tag reuse.
Uses only isolated temp homes and monkeypatched env; never touches the real
``SUPERMEMORY_API_KEY`` value and never performs network I/O.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.supermemory as sm


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    monkeypatch.delenv("SUPERMEMORY_CONTAINER_TAG", raising=False)
    return home


def _write_config(home: Path, data: dict) -> None:
    import yaml

    (home / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


# ── Provider activation/config ────────────────────────────────────────────

def test_set_memory_provider_accepts_supermemory(isolated_home, monkeypatch):
    import api.config as config

    monkeypatch.setattr(config, "_get_config_path", lambda: isolated_home / "config.yaml")
    monkeypatch.setattr(config, "reload_config", lambda: None)
    monkeypatch.setattr(config, "get_config", lambda: config._load_yaml_config_file(isolated_home / "config.yaml"))
    assert sm.set_memory_provider("supermemory") == "supermemory"
    assert sm.set_memory_provider("Supermemory") == "supermemory"
    assert sm.get_memory_provider(isolated_home) == "supermemory"


def test_set_memory_provider_rejects_unknown_slugs(isolated_home, monkeypatch):
    import api.config as config

    monkeypatch.setattr(config, "_get_config_path", lambda: isolated_home / "config.yaml")
    for bad in ("mem0", "honcho", "", "openai", "supermemory2"):
        with pytest.raises(ValueError):
            sm.set_memory_provider(bad)


def test_status_never_exposes_key_value(isolated_home, monkeypatch):
    monkeypatch.setenv("SUPERMEMORY_API_KEY", "sm_test_secret_value")
    _write_config(isolated_home, {"memory": {"provider": "supermemory"}})
    status = sm.get_memory_provider_status(profile_home=isolated_home, profile_name="default")
    assert status["enabled"] is True
    assert status["key_configured"] is True
    serialized = json.dumps(status)
    assert "sm_test_secret_value" not in serialized
    assert "SUPERMEMORY_API_KEY" not in serialized or "sm_test_secret_value" not in serialized
    for value in status.values():
        assert value != "sm_test_secret_value"


def test_status_reports_key_from_profile_env_file(isolated_home):
    (isolated_home / ".env").write_text("SUPERMEMORY_API_KEY=sm_file_key\n", encoding="utf-8")
    assert sm.is_supermemory_key_configured(isolated_home) is True
    status = sm.get_memory_provider_status(profile_home=isolated_home, profile_name="default")
    assert status["key_configured"] is True
    assert "sm_file_key" not in json.dumps(status)


# ── Profile mapping (item 5) ──────────────────────────────────────────────

def test_root_default_maps_to_shared_hermes():
    assert sm.expected_container_tag_for_profile("default", isolated=False) == "hermes"
    assert sm.expected_container_tag_for_profile("", isolated=False) == "hermes"


def test_named_profiles_map_to_hermes_colon_profile():
    assert sm.expected_container_tag_for_profile("coder", isolated=False) == "hermes:coder"
    assert sm.expected_container_tag_for_profile("researcher", isolated=False) == "hermes:researcher"
    tag = sm.expected_container_tag_for_profile("coder", isolated=False)
    assert sm.is_valid_container_tag(tag)
    assert len(tag) <= 100


def test_profile_tags_are_distinct_per_profile():
    assert sm.expected_container_tag_for_profile("alice") != sm.expected_container_tag_for_profile("bob")
    assert sm.expected_container_tag_for_profile("coder") != "hermes"


# ── Invalid names: sanitization or rejection ──────────────────────────────

def test_invalid_container_tags_rejected():
    for bad in ("", "has space", "a/b", "team@acme", "x" * 101, None, 123):
        assert sm.is_valid_container_tag(bad) is False
    for good in ("hermes", "hermes:coder", "user_123", "org:acme:user:john", "a-b_c:d"):
        assert sm.is_valid_container_tag(good) is True


def test_sanitize_profile_identity():
    assert sm.sanitize_profile_identity("") == "default"
    assert sm.sanitize_profile_identity("   ") == "default"
    assert sm.sanitize_profile_identity("coder") == "coder"
    cleaned = sm.sanitize_profile_identity("User@Home!Space")
    assert sm.is_valid_container_tag(f"hermes:{cleaned}")
    assert " " not in cleaned and "@" not in cleaned and "!" not in cleaned
    long_name = "x" * 200
    assert len(sm.sanitize_profile_identity(long_name)) <= 80


def test_build_payload_uses_singular_container_tag():
    payload = sm.build_container_tag_payload("hermes:coder")
    assert payload == {"containerTag": "hermes:coder"}
    assert "containerTags" not in payload
    with pytest.raises(ValueError):
        sm.build_container_tag_payload("has space")
    with pytest.raises(ValueError):
        sm.build_container_tag_payload("")


# ── Isolated-profile behavior (item 7) ────────────────────────────────────

def test_isolated_mode_forces_scoped_tag_even_for_root():
    assert sm.expected_container_tag_for_profile("default", isolated=True) == "hermes:default"
    assert sm.expected_container_tag_for_profile("", isolated=True) == "hermes:default"


def test_ensure_replaces_shared_tag_when_scoped_required(isolated_home, monkeypatch):
    monkeypatch.setattr(sm, "_is_isolated_mode", lambda: False)
    (isolated_home / "supermemory.json").write_text(json.dumps({"container_tag": "hermes"}), encoding="utf-8")
    result = sm.ensure_profile_scoped_supermemory_config(
        profile_home=isolated_home / "profiles" / "coder", profile_name="coder"
    )
    assert result["container_tag"] == "hermes:coder"
    assert result["replaced"] is True
    stored = json.loads((isolated_home / "profiles" / "coder" / "supermemory.json").read_text(encoding="utf-8"))
    assert stored["container_tag"] == "hermes:coder"


def test_ensure_preserves_matching_tag_and_sets_0600(isolated_home):
    target_home = isolated_home / "profiles" / "coder"
    target_home.mkdir(parents=True)
    (target_home / "supermemory.json").write_text(
        json.dumps({"container_tag": "hermes:coder", "auto_recall": False}), encoding="utf-8"
    )
    result = sm.ensure_profile_scoped_supermemory_config(profile_home=target_home, profile_name="coder")
    assert result["container_tag"] == "hermes:coder"
    assert result["replaced"] is False
    # Native keys preserved.
    stored = json.loads((target_home / "supermemory.json").read_text(encoding="utf-8"))
    assert stored["auto_recall"] is False
    assert (target_home / "supermemory.json").stat().st_mode & 0o777 == 0o600


# ── Cross-profile tag reuse prevention ────────────────────────────────────

def test_cross_profile_tags_never_reused(isolated_home):
    alice = isolated_home / "profiles" / "alice"
    bob = isolated_home / "profiles" / "bob"
    sm.ensure_profile_scoped_supermemory_config(profile_home=alice, profile_name="alice")
    result_bob = sm.ensure_profile_scoped_supermemory_config(profile_home=bob, profile_name="bob")
    tag_alice = json.loads((alice / "supermemory.json").read_text(encoding="utf-8"))["container_tag"]
    assert tag_alice == "hermes:alice"
    assert result_bob["container_tag"] == "hermes:bob"
    assert tag_alice != result_bob["container_tag"]
    assert sm.find_cross_profile_tag_reuse(profile_home=bob, profile_name="bob") == []


def test_colliding_custom_tag_is_replaced(isolated_home):
    alice = isolated_home / "profiles" / "alice"
    bob = isolated_home / "profiles" / "bob"
    alice.mkdir(parents=True)
    bob.mkdir(parents=True)
    (alice / "supermemory.json").write_text(json.dumps({"container_tag": "hermes:alice"}), encoding="utf-8")
    # Bob manually configured to reuse Alice's tag -- strict isolation replaces it.
    (bob / "supermemory.json").write_text(json.dumps({"container_tag": "hermes:alice"}), encoding="utf-8")
    result = sm.ensure_profile_scoped_supermemory_config(profile_home=bob, profile_name="bob")
    assert result["container_tag"] == "hermes:bob"
    assert result["replaced"] is True


def test_search_helper_has_no_foreign_tag_argument():
    import inspect

    sig = inspect.signature(sm.search_own_container)
    assert "container_tag" not in sig.parameters
    assert "containerTag" not in sig.parameters
    assert "profile_name" in sig.parameters


def test_memory_provider_route_rejects_key_in_body():
    import api.routes as routes
    from unittest.mock import MagicMock

    captured = {}

    def fake_bad(handler, msg, status=400):
        captured["msg"] = msg
        captured["status"] = status
        return False

    handler = MagicMock()
    orig_bad = routes.bad
    routes.bad = fake_bad
    try:
        assert routes._handle_memory_provider(handler, {"provider": "supermemory", "api_key": "sm_x"}) is False
        assert captured["status"] == 400
        assert routes._handle_memory_provider(handler, {"provider": "honcho"}) is False
    finally:
        routes.bad = orig_bad


def test_memory_read_includes_provider_status_without_key(isolated_home, monkeypatch):
    import api.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setenv("SUPERMEMORY_API_KEY", "sm_probe_secret")
    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        return True

    handler = MagicMock()
    orig_j = routes.j
    routes.j = fake_j
    try:
        assert routes._handle_memory_read(handler, parsed=None) is True
    finally:
        routes.j = orig_j
    # Local file behavior preserved.
    assert "memory" in captured["payload"] and "user" in captured["payload"] and "soul" in captured["payload"]
    provider = captured["payload"].get("memory_provider")
    assert isinstance(provider, dict)
    assert "sm_probe_secret" not in json.dumps(captured["payload"])
