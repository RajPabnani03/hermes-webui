"""A provider belongs to a model selection, not every future model in a chat."""

from types import SimpleNamespace

import pytest

from api import routes


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(routes, "get_available_models", lambda: {
        "active_provider": "nous",
        "default_model": "deepseek/deepseek-v4.1-flash",
        "groups": [{"provider_id": "nous", "models": [
            {"id": "deepseek/deepseek-v4.1-flash"},
        ]}],
    })


def test_changed_model_does_not_inherit_old_provider(catalog):
    assert routes._session_model_state_from_request(
        "deepseek/deepseek-v4.1-flash", None, "openrouter",
        current_model="anthropic/claude-sonnet-4",
    ) == ("deepseek/deepseek-v4.1-flash", None)


@pytest.mark.parametrize("requested,expected", [(None, "openrouter"), ("custom:lab", "custom:lab")])
def test_unchanged_model_or_explicit_provider_is_preserved(catalog, requested, expected):
    assert routes._session_model_state_from_request(
        "deepseek/deepseek-v4.1-flash", requested, "openrouter",
        current_model="deepseek/deepseek-v4.1-flash",
    ) == ("deepseek/deepseek-v4.1-flash", expected)


@pytest.mark.parametrize("body,expected", [
    ({"model": "new-model"}, None),
    ({"model": "old-model"}, "openrouter"),
    ({}, "openrouter"),
    ({"model": ""}, "openrouter"),
    ({"model": "new-model", "model_provider": "custom:lab"}, "custom:lab"),
    ({"model": "old-model", "model_provider": None}, None),
])
def test_chat_provider_fallback_is_tied_to_unchanged_model(body, expected):
    session = SimpleNamespace(model="old-model", model_provider="openrouter")
    assert routes._requested_session_model_provider(body, session) == expected


def test_session_update_persists_new_model_without_old_provider(monkeypatch, catalog):
    from contextlib import nullcontext

    saved = []
    session = SimpleNamespace(
        session_id="7585", model="anthropic/claude-sonnet-4",
        model_provider="openrouter", workspace="/tmp", messages=[],
    )
    session.save = lambda: saved.append((session.model, session.model_provider))
    session.compact = lambda: {"model": session.model, "model_provider": session.model_provider}
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *a, **kw: False)
    monkeypatch.setattr(routes, "read_body", lambda handler: {
        "session_id": "7585", "model": "deepseek/deepseek-v4.1-flash",
    })
    monkeypatch.setattr(routes, "_get_or_materialize_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda ws: ws)
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda sid: nullcontext())
    monkeypatch.setattr(routes, "_resolve_context_length_for_session_model", lambda *a: 64000)
    monkeypatch.setattr(routes, "set_last_workspace", lambda ws: None)
    monkeypatch.setattr(routes, "j", lambda handler, payload, **kw: payload)

    response = routes.handle_post(object(), SimpleNamespace(path="/api/session/update"))

    assert saved == [("deepseek/deepseek-v4.1-flash", None)]
    assert response["session"]["model_provider"] is None
    assert session.context_length == 64000
    assert session.last_prompt_tokens == 0


def test_chat_start_resolves_new_model_using_profile_not_old_provider(monkeypatch, catalog):
    session = SimpleNamespace(
        session_id="7585", model="anthropic/claude-sonnet-4",
        model_provider="openrouter", workspace="/tmp", messages=[], profile="default",
    )
    monkeypatch.setattr(routes, "_get_or_materialize_session", lambda *a, **kw: session)
    monkeypatch.setattr(routes, "_session_visible_to_active_profile", lambda *a: True)
    monkeypatch.setattr(routes, "_resolve_chat_workspace_with_recovery", lambda *a: "/tmp")
    monkeypatch.setattr(routes, "_read_profile_model_config", lambda s, provider: (
        ("nous", "deepseek/deepseek-v4.1-flash", {}) if provider is None else (None, None, {})
    ))
    started = []
    monkeypatch.setattr(routes, "_start_run", lambda s, **kw: started.append(kw) or {})
    monkeypatch.setattr(routes, "j", lambda handler, payload, **kw: payload)

    routes._handle_chat_start(object(), {
        "session_id": "7585", "model": "deepseek/deepseek-v4.1-flash", "message": "hello",
    })

    assert started[0]["model"] == "deepseek/deepseek-v4.1-flash"
    assert started[0]["model_provider"] == "nous"
