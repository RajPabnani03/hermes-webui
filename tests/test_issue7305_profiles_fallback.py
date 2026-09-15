"""Regression tests for #7305: GET /api/profiles must not 500 when Agent is unmounted.

In the two-container / gateway deployment, hermes_cli and agent.skill_utils are
both absent. list_profiles_api() already catches a missing hermes_cli and falls
back to _default_profile_dict(), but that path used to import agent.skill_utils
unguarded from _compute_profile_skills_stats() and crash.

When Agent skill utilities are unavailable, skill counts are unknown: a stable
(0, 0) pair. Do not invent a local SKILL.md walker that can drift from Agent
exclusions.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import api.profiles as profiles


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _block_imports(monkeypatch, names: set[str]) -> None:
    """Force ImportError for the given top-level / dotted module names."""
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name in names or any(name.startswith(n + ".") for n in names):
            raise ImportError(f"blocked {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for n in names:
        monkeypatch.delitem(sys.modules, n, raising=False)


@pytest.fixture(autouse=True)
def _clear_profile_caches():
    profiles._SKILLS_STATS_CACHE.clear()
    profiles._invalidate_list_profiles_cache()
    yield
    profiles._SKILLS_STATS_CACHE.clear()
    profiles._invalidate_list_profiles_cache()


def test_compute_skill_stats_unknown_when_agent_unmounted(monkeypatch, tmp_path):
    """A real skills/ tree must not raise, and must report unknown (0, 0)."""
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    _block_imports(monkeypatch, {"agent.skill_utils"})

    enabled, compatible = profiles._compute_profile_skills_stats(tmp_path)
    assert (enabled, compatible) == (0, 0)

    enabled, compatible = profiles._get_profile_skills_stats(tmp_path)
    assert (enabled, compatible) == (0, 0)


def test_default_profile_dict_never_raises_without_agent(monkeypatch, tmp_path):
    _write_skill(tmp_path, "alpha")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", tmp_path)
    _block_imports(monkeypatch, {"agent.skill_utils", "hermes_cli", "hermes_cli.profiles"})

    row = profiles._default_profile_dict()
    assert row["name"] == "default"
    assert row["is_default"] is True
    assert row["is_active"] is True
    assert row["skill_count"] == 0
    assert row["enabled_skills"] == 0
    assert row["total_skills"] == 0
    assert row["path"] == str(tmp_path)


def test_list_profiles_api_fallback_returns_default_row_without_agent(monkeypatch, tmp_path):
    """hermes_cli missing + skills present → default-only row, no 500."""
    _write_skill(tmp_path, "alpha")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", tmp_path)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_build_profile_rows_fast", lambda: None)
    _block_imports(monkeypatch, {"agent.skill_utils", "hermes_cli", "hermes_cli.profiles"})

    rows = profiles.list_profiles_api()
    assert len(rows) == 1
    assert rows[0]["name"] == "default"
    assert rows[0]["is_active"] is True
    assert rows[0]["skill_count"] == 0
    assert rows[0]["total_skills"] == 0


def test_isolated_mode_fallback_does_not_raise_without_agent(monkeypatch, tmp_path):
    _write_skill(tmp_path, "alpha")
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: True)
    monkeypatch.setattr(profiles, "_isolated_profile_name", lambda: "work")
    monkeypatch.setattr(profiles, "_INITIAL_HERMES_HOME", str(tmp_path))
    _block_imports(monkeypatch, {"agent.skill_utils", "hermes_cli", "hermes_cli.profiles"})

    rows = profiles.list_profiles_api()
    assert len(rows) == 1
    assert rows[0]["name"] == "work"
    assert rows[0]["is_active"] is True
    assert rows[0]["skill_count"] == 0


def test_profiles_route_survives_agentless_fallback(monkeypatch, tmp_path):
    """GET /api/profiles must return 200 with a usable default row."""
    import api.routes as routes

    _write_skill(tmp_path, "alpha")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", tmp_path)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_build_profile_rows_fast", lambda: None)
    monkeypatch.setattr(routes, "_is_isolated_profile_mode", lambda: False)
    _block_imports(monkeypatch, {"agent.skill_utils", "hermes_cli", "hermes_cli.profiles"})

    captured = {}

    def _j(_handler, payload, status=200):
        captured["status"] = status
        captured["payload"] = payload
        return True

    monkeypatch.setattr(routes, "j", _j)
    result = routes.handle_get(SimpleNamespace(), urlparse("/api/profiles"))
    assert result is True
    assert captured["status"] == 200
    body = captured["payload"]
    assert body["active"] == "default"
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["name"] == "default"
    assert body["profiles"][0]["skill_count"] == 0
