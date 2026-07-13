"""Regression tests for #5420 — profile switch breaks /api/session/new.

Two related bugs:
1. Local `from api.profiles import get_active_profile_name` inside handle_post()
   shadowed the module-level import, causing UnboundLocalError on code paths
   that ran before those branches.
2. Cross-profile prev_session_id after profile switch returned 404 instead of
   creating the new session.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock
from urllib.parse import urlparse

import api.routes as routes


def _handle_post_source() -> str:
    return inspect.getsource(routes.handle_post)


def test_handle_post_has_no_local_get_active_profile_name_imports():
    """handle_post must not re-import get_active_profile_name locally (#5420)."""
    tree = ast.parse(_handle_post_source())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "api.profiles":
            for alias in node.names:
                if alias.name == "get_active_profile_name":
                    offenders.append(node.lineno)
    assert offenders == [], (
        "Local imports of get_active_profile_name inside handle_post shadow the "
        f"module binding and can cause UnboundLocalError. Found at lines: {offenders}"
    )


def test_session_new_does_not_shadow_get_active_profile_name(monkeypatch):
    """handle_post /api/session/new must not raise UnboundLocalError (#5420)."""
    handler = MagicMock()
    handler.headers = {}
    calls = {"new": 0}

    class _NewSession:
        session_id = "fresh"
        messages = []

        def compact(self):
            return {"session_id": self.session_id}

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
    monkeypatch.setattr(
        routes,
        "new_session",
        lambda **_kwargs: calls.__setitem__("new", calls["new"] + 1) or _NewSession(),
    )
    monkeypatch.setattr(routes, "get_last_workspace", lambda: None)

    cap = {}
    monkeypatch.setattr(routes, "j", lambda _handler, payload: cap.__setitem__("ok", payload) or True)

    routes.handle_post(handler, urlparse("/api/session/new"))
    assert calls["new"] == 1
    assert cap["ok"]["session"]["session_id"] == "fresh"
