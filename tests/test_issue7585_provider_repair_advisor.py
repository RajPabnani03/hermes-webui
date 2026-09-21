"""#7585 repair advisor: Jev-powered provider suggestions for ambiguous sessions.

The advisor is read-only and degrades gracefully: no API key, missing SDK,
or API failure all yield "no suggestion" instead of an error, so Jev can
never block chat or session updates. Live TypeSafe calls are never made
here — the client factory is stubbed.
"""

from types import SimpleNamespace

import pytest

from api import routes


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(routes, "get_available_models", lambda **kw: {
        "active_provider": "nous",
        "default_model": "nous-model",
        "groups": [
            {"provider_id": "nous", "models": [{"id": "shared-model"}]},
            {"provider_id": "openrouter", "models": [
                {"id": "shared-model"},
                {"id": "solo-model"},
            ]},
            {"provider_id": "custom:lab", "models": [{"id": "lab-model"}]},
        ],
    })


def _session(model, provider):
    return SimpleNamespace(model=model, model_provider=provider, profile=None)


class _FakeAnswer:
    def __init__(self, choice, confidence):
        self.choice = choice
        self.confidence = confidence


class _FakeClient:
    """Stands in for TypeSafeClient (context-manager protocol + system_one)."""

    def __init__(self, choice="openrouter", confidence=0.9, exc=None):
        self.choice = choice
        self.confidence = confidence
        self.exc = exc
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(answers={
            "intended_provider": _FakeAnswer(self.choice, self.confidence),
        })


def test_sole_candidate_needs_no_jev(catalog, monkeypatch):
    def _boom():
        raise AssertionError("Jev must not be consulted for a sole candidate")

    monkeypatch.setattr(routes, "_get_typesafe_client", _boom)
    suggestion = routes._suggest_session_provider(_session("solo-model", None))
    assert suggestion["method"] == "sole-candidate"
    assert suggestion["suggested_provider"] == "openrouter"
    assert suggestion["confidence"] == 1.0
    assert suggestion["uncertain"] is False


def test_jev_high_confidence_suggestion(catalog, monkeypatch):
    client = _FakeClient(choice="openrouter", confidence=0.9)
    monkeypatch.setattr(routes, "_get_typesafe_client", lambda: client)
    suggestion = routes._suggest_session_provider(_session("shared-model", None))
    assert suggestion["method"] == "jev-choice"
    assert suggestion["suggested_provider"] == "openrouter"
    assert suggestion["confidence"] == pytest.approx(0.9)
    assert suggestion["uncertain"] is False
    assert set(suggestion["candidates"]) == {"nous", "openrouter"}
    # One Choice question over named state fields.
    (call,) = client.calls
    assert set(call["questions"]) == {"intended_provider"}
    assert call["state"]["session"]["model"] == "shared-model"


def test_jev_low_confidence_is_flagged_uncertain(catalog, monkeypatch):
    monkeypatch.setattr(
        routes, "_get_typesafe_client",
        lambda: _FakeClient(choice="nous", confidence=0.4),
    )
    suggestion = routes._suggest_session_provider(_session("shared-model", None))
    assert suggestion["method"] == "jev-choice"
    assert suggestion["suggested_provider"] == "nous"
    assert suggestion["uncertain"] is True


def test_no_key_means_unavailable_not_error(catalog, monkeypatch):
    monkeypatch.setattr(routes, "_get_typesafe_client", lambda: None)
    suggestion = routes._suggest_session_provider(_session("shared-model", None))
    assert suggestion["method"] == "unavailable"
    assert suggestion["suggested_provider"] is None


def test_jev_failure_means_unavailable_not_error(catalog, monkeypatch):
    monkeypatch.setattr(
        routes, "_get_typesafe_client",
        lambda: _FakeClient(exc=RuntimeError("boom")),
    )
    suggestion = routes._suggest_session_provider(_session("shared-model", None))
    assert suggestion["method"] == "unavailable"
    assert suggestion["suggested_provider"] is None


def test_jev_choice_outside_candidates_is_rejected(catalog, monkeypatch):
    monkeypatch.setattr(
        routes, "_get_typesafe_client",
        lambda: _FakeClient(choice="custom:evil", confidence=0.99),
    )
    suggestion = routes._suggest_session_provider(_session("shared-model", None))
    assert suggestion["suggested_provider"] is None
    assert suggestion["method"] == "unavailable"


def test_explicit_selection_needs_no_repair(catalog, monkeypatch):
    def _boom():
        raise AssertionError("Jev must not be consulted for an explicit selection")

    monkeypatch.setattr(routes, "_get_typesafe_client", _boom)
    suggestion = routes._suggest_session_provider(_session("shared-model", "nous"))
    assert suggestion["method"] == "already-explicit"
    assert suggestion["suggested_provider"] is None


def test_unknown_model_is_unrepairable(catalog):
    suggestion = routes._suggest_session_provider(_session("nope-model", None))
    assert suggestion["method"] == "unrepairable"
    assert suggestion["suggested_provider"] is None


def test_missing_model_is_unrepairable(catalog):
    suggestion = routes._suggest_session_provider(_session("", None))
    assert suggestion["method"] == "unrepairable"


def test_bare_model_matches_qualified_catalog_entries():
    catalog = {"groups": [
        {"provider_id": "openai", "models": [{"id": "openai/gpt-4"}]},
        {"provider_id": "openrouter", "models": [{"id": "gpt-4"}]},
    ]}
    assert routes._provider_candidates_for_model("gpt-4", catalog=catalog) == [
        "openrouter", "openai",
    ]


def test_suggestion_endpoint_is_read_only(catalog, monkeypatch):
    session = _session("solo-model", None)
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *a, **kw: False)
    monkeypatch.setattr(routes, "read_body", lambda handler: {"session_id": "7585"})
    monkeypatch.setattr(routes, "_get_or_materialize_session", lambda sid: session)
    monkeypatch.setattr(routes, "j", lambda handler, payload, **kw: payload)

    response = routes.handle_post(object(), SimpleNamespace(path="/api/session/provider-suggestion"))

    assert response["suggestion"]["suggested_provider"] == "openrouter"
    # Read-only: the session object is untouched.
    assert (session.model, session.model_provider) == ("solo-model", None)
