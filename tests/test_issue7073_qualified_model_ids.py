"""Regression coverage for issue #7073 qualified Ollama / custom model IDs."""

from __future__ import annotations

import api.config as config


OLLAMA_CONFIG = {
    "model": {
        "provider": "ollama",
        "default": "hermes-reasoner:latest",
        "base_url": "http://10.9.194.141:11434/v1",
    },
    "providers": {
        "ollama": {
            "base_url": "http://10.9.194.141:11434/v1",
            "models": ["hermes-reasoner:latest"],
        }
    },
}

NAMED_CUSTOM_CONFIG = {
    "model": {
        "provider": "custom:backup",
        "default": "model-a",
        "base_url": "https://backup.example/v1",
    },
    "custom_providers": [
        {
            "name": "backup",
            "base_url": "https://backup.example/v1",
            "models": ["model-a", "model-a:free"],
        }
    ],
}


def test_fail_before_reported_custom_latest_split():
    parsed = config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest",
        {},
    )
    assert parsed == ("latest", "custom:hermes-reasoner")


def test_declared_ollama_tag_is_not_split_to_latest(monkeypatch):
    monkeypatch.setattr(config, "cfg", OLLAMA_CONFIG)
    parsed = config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest",
        OLLAMA_CONFIG,
    )
    assert parsed == ("hermes-reasoner:latest", "custom")


def test_resolve_declared_ollama_tag_keeps_owner_and_full_id(monkeypatch):
    monkeypatch.setattr(config, "cfg", OLLAMA_CONFIG)
    model, provider, base_url = config.resolve_model_provider(
        "@custom:hermes-reasoner:latest"
    )
    assert model == "hermes-reasoner:latest"
    assert provider == "ollama"
    assert base_url == "http://10.9.194.141:11434/v1"


def test_bare_ollama_qualifier_preserves_colon_tag():
    parsed = config._parse_provider_qualified_model_id(
        "@ollama:hermes-reasoner:latest"
    )
    assert parsed == ("hermes-reasoner:latest", "ollama")


def test_live_ollama_tag_peels_even_when_not_in_models_list(monkeypatch):
    owner = {
        "model": {
            "provider": "ollama",
            "default": "qwen2.5:14b",
            "base_url": "http://10.9.194.141:11434/v1",
        },
        "providers": {"ollama": {"base_url": "http://10.9.194.141:11434/v1"}},
    }
    monkeypatch.setattr(config, "cfg", owner)
    assert config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest",
        owner,
    ) == ("hermes-reasoner:latest", "custom")
    model, provider, base_url = config.resolve_model_provider(
        "@custom:hermes-reasoner:latest"
    )
    assert model == "hermes-reasoner:latest"
    assert provider == "ollama"
    assert base_url == "http://10.9.194.141:11434/v1"


def test_keyed_cloud_owner_does_not_peel_unregistered_custom_slug():
    owner = {"model": {"provider": "openai", "default": "gpt-5.4"}}
    assert config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest",
        owner,
    ) == ("latest", "custom:hermes-reasoner")


def test_generic_custom_hyphen_slug_is_not_eaten_as_model_tag():
    owner = {"model": {"provider": "custom", "base_url": "http://127.0.0.1:15721/v1"}}
    assert config._parse_provider_qualified_model_id(
        "@custom:local-127.0.0.1-15721:deepseek-v4-flash",
        owner,
    ) == ("deepseek-v4-flash", "custom:local-127.0.0.1-15721")


def test_named_custom_provider_still_owns_plain_and_tagged_models():
    assert config._parse_provider_qualified_model_id(
        "@custom:backup:model-a",
        NAMED_CUSTOM_CONFIG,
    ) == ("model-a", "custom:backup")
    assert config._parse_provider_qualified_model_id(
        "@custom:backup:model-a:free",
        NAMED_CUSTOM_CONFIG,
    ) == ("model-a:free", "custom:backup")


def test_unregistered_custom_backup_shape_stays_named_provider():
    assert config._parse_provider_qualified_model_id(
        "@custom:backup:model-a",
        {},
    ) == ("model-a", "custom:backup")
    assert config._parse_provider_qualified_model_id(
        "@custom:backup:model-a:free",
        {},
    ) == ("model-a:free", "custom:backup")


def test_host_port_custom_provider_is_not_eaten_as_model_tag():
    assert config._parse_provider_qualified_model_id(
        "@custom:10.9.194.141:11434:hermes-reasoner:latest",
        OLLAMA_CONFIG,
    ) == ("hermes-reasoner:latest", "custom:10.9.194.141:11434")
    assert config._parse_provider_qualified_model_id(
        "@custom:localhost:11434:llama3.2",
        {},
    ) == ("llama3.2", "custom:localhost:11434")


def test_openrouter_free_tag_and_slash_model_stay_intact():
    assert config._parse_provider_qualified_model_id(
        "@openrouter:meta/llama-4:free"
    ) == ("meta/llama-4:free", "openrouter")


def test_named_custom_provider_wins_over_declared_ollama_tag():
    owner = {
        **OLLAMA_CONFIG,
        "custom_providers": [{"name": "hermes-reasoner", "models": ["latest"]}],
    }
    assert config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest",
        owner,
    ) == ("latest", "custom:hermes-reasoner")
