import builtins

from api import config


class _DummyModule:
    pass


def test_verify_hermes_imports_checks_openai_pydantic_core(monkeypatch):
    """Startup diagnostics should include the lazy OpenAI client dependency stack."""
    requested = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"run_agent", "openai", "pydantic_core._pydantic_core"}:
            requested.append(name)
            return _DummyModule()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    ok, missing, errors = config.verify_hermes_imports()

    assert ok is True
    assert missing == []
    assert errors == {}
    assert requested == ["run_agent", "openai", "pydantic_core._pydantic_core"]


def test_verify_hermes_imports_reports_missing_pydantic_core_extension(monkeypatch):
    """Regression for first-chat OpenAI init failures from missing pydantic-core wheels."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"run_agent", "openai"}:
            return _DummyModule()
        if name == "pydantic_core._pydantic_core":
            raise ModuleNotFoundError("No module named 'pydantic_core._pydantic_core'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    ok, missing, errors = config.verify_hermes_imports()

    assert ok is False
    assert missing == ["pydantic_core._pydantic_core"]
    assert "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'" in errors[
        "pydantic_core._pydantic_core"
    ]
