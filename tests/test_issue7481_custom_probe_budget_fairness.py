"""#7481: one unreachable custom endpoint must not starve later probes.

The cold catalog rebuild probes ``model.base_url`` first, then named
``custom_providers`` serially. Each probe used the full 5s cap, which is
larger than the 4s foreground rebuild budget, so a dead active endpoint
consumed the whole window and reachable providers behind it were never
probed in-band.

These tests pin the *timeout contract* (fair share of the current budget)
rather than wall-clock publication. Open PR #7506 also attacks generation
fencing / disk races; this module does not — it only covers scheduling
isolation at the probe timeout seam.
"""

from __future__ import annotations

import json
import socket
import urllib.request

import pytest

import api.config as cfg


@pytest.fixture(autouse=True)
def isolate_models_catalog_state(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model: {}\n", encoding="utf-8")
    auth_store_path = tmp_path / "auth.json"
    auth_store_path.write_text("{}", encoding="utf-8")
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("", encoding="utf-8")

    monkeypatch.setattr(cfg, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(cfg, "_cfg_path", config_path, raising=False)
    monkeypatch.setattr(cfg, "_cfg_mtime", config_path.stat().st_mtime, raising=False)
    monkeypatch.setattr(cfg, "_cfg_has_in_memory_overrides", lambda: True)
    monkeypatch.setattr(cfg, "_get_auth_store_path", lambda: auth_store_path)
    monkeypatch.setattr(cfg, "_load_models_cache_from_disk", lambda: None)
    monkeypatch.setattr(cfg, "_save_models_cache_to_disk", lambda *_a, **_k: None)
    monkeypatch.setattr(cfg, "_get_models_cache_path", lambda: tmp_path / "models_cache.json")
    monkeypatch.setattr(cfg, "_delete_models_cache_on_disk", lambda: None)
    monkeypatch.setattr(
        cfg,
        "_models_cache_source_fingerprint",
        lambda: "unit-test-fingerprint",
    )
    monkeypatch.setattr(cfg, "_available_models_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_available_models_cache_ts", 0.0, raising=False)
    monkeypatch.setattr(cfg, "_available_models_live_rebuild_ts", 0.0, raising=False)
    monkeypatch.setattr(
        cfg,
        "_available_models_cache_source_fingerprint",
        None,
        raising=False,
    )
    monkeypatch.setattr(cfg, "_cache_build_in_progress", False, raising=False)
    monkeypatch.setattr(cfg, "cfg", {}, raising=False)
    # Skip Copilot/OpenRouter/Nous live probes that are outside this issue.
    monkeypatch.setattr(cfg, "_read_live_provider_model_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [])

    yield {"auth_store_path": auth_store_path}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _install_urlopen(monkeypatch, handler):
    calls: list[dict] = []

    def fake_urlopen(req, timeout=None, **_kwargs):
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: str(req))()
        calls.append({"url": str(url), "timeout": timeout})
        return handler(url, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


@pytest.mark.parametrize(
    ("count", "budget", "cap", "expected"),
    [
        (1, 4.0, 5.0, 4.0),
        (2, 4.0, 5.0, 2.0),
        (8, 4.0, 5.0, 0.5),
        (401, 4.0, 5.0, 4.0 / 401),
        (0, 4.0, 5.0, 5.0),
        (2, 0.0, 5.0, 5.0),
        (2, -1.0, 5.0, 5.0),
        (1, 10.0, 5.0, 5.0),
    ],
)
def test_fair_timeout_cannot_outspend_the_window(count, budget, cap, expected):
    got = cfg._fair_custom_probe_timeout(count, budget=budget, cap=cap)
    assert got == pytest.approx(expected)
    if budget > 0 and count > 0:
        assert got * count <= budget + 1e-9
        assert 0 < got <= cap


def test_static_allowlist_entries_do_not_dilute_the_probe_count():
    count = cfg._count_serial_custom_catalog_probes(
        {
            "model": {"base_url": "http://192.168.1.9:1234/v1"},
            "custom_providers": [
                {
                    "name": "My Gateway",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "gw-key",
                },
                {
                    "name": "Pinned",
                    "base_url": "https://pinned.example.com/v1",
                    "models": ["only-this"],
                },
                {"name": "No URL"},
                {"base_url": "https://unnamed.example.com/v1"},
            ],
        }
    )
    # Active endpoint + the one named provider without a models allowlist.
    assert count == 2
    assert cfg._fair_custom_probe_timeout(count, budget=4.0, cap=5.0) == 2.0


def test_unreachable_active_endpoint_does_not_use_the_full_cap_before_the_gateway(
    monkeypatch,
    isolate_models_catalog_state,
):
    """Dead LAN active endpoint must still leave a timeout slice for the gateway.

    Pre-fix, both probes received timeout=5.0; the 4s budget expired during the
    first connect and the gateway URL was never requested. Post-fix both probes
    receive budget/2 and the gateway call still happens.
    """
    monkeypatch.setattr(cfg, "_LIVE_REBUILD_BUDGET_SECONDS", 4.0, raising=False)
    cfg.cfg = {
        "model": {
            "provider": "lmstudio",
            "default": "local-model",
            "base_url": "http://192.168.1.9:1234/v1",
        },
        "providers": {},
        "fallback_providers": [],
        "custom_providers": [
            {
                "name": "My Gateway",
                "base_url": "https://api.example.com/v1",
                "api_key": "gw-key",
            }
        ],
    }

    def handler(url, timeout):
        if "192.168.1.9" in url:
            raise OSError("unreachable LAN endpoint")
        if "api.example.com" in url:
            return _FakeResponse(
                {
                    "data": [
                        {"id": "gateway-model-a", "name": "Gateway A"},
                        {"id": "gateway-model-b", "name": "Gateway B"},
                    ]
                }
            )
        raise AssertionError(f"unexpected urlopen: {url}")

    calls = _install_urlopen(monkeypatch, handler)
    result = cfg.get_available_models(force_refresh=True)

    lan_calls = [c for c in calls if "192.168.1.9" in c["url"]]
    gw_calls = [c for c in calls if "api.example.com" in c["url"]]
    assert lan_calls, "active endpoint must still be probed first"
    assert gw_calls, "reachable named provider must still be probed in-band"
    for call in lan_calls + gw_calls:
        assert call["timeout"] == pytest.approx(2.0)
        assert call["timeout"] < cfg.CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS

    group_ids = [g.get("provider_id") for g in result.get("groups", [])]
    assert "custom:my-gateway" in group_ids
    gateway = next(g for g in result["groups"] if g.get("provider_id") == "custom:my-gateway")
    model_ids = [m["id"] for m in gateway.get("models", [])]
    assert any("gateway-model-a" in mid for mid in model_ids)
    assert any("gateway-model-b" in mid for mid in model_ids)


def test_multiple_dead_endpoints_still_probe_the_live_one(monkeypatch, isolate_models_catalog_state):
    monkeypatch.setattr(cfg, "_LIVE_REBUILD_BUDGET_SECONDS", 4.0, raising=False)
    cfg.cfg = {
        "model": {"provider": "custom", "base_url": "http://dead-active.example/v1"},
        "providers": {},
        "fallback_providers": [],
        "custom_providers": [
            {"name": "Dead One", "base_url": "http://dead-one.example/v1", "api_key": "k1"},
            {"name": "Live Two", "base_url": "https://live-two.example/v1", "api_key": "k2"},
        ],
    }

    def handler(url, timeout):
        if "live-two.example" in url:
            return _FakeResponse({"data": [{"id": "live-ok", "name": "Live OK"}]})
        raise OSError("unreachable")

    calls = _install_urlopen(monkeypatch, handler)
    result = cfg.get_available_models(force_refresh=True)

    assert any("dead-active.example" in c["url"] for c in calls)
    assert any("dead-one.example" in c["url"] for c in calls)
    assert any("live-two.example" in c["url"] for c in calls)
    expected = pytest.approx(4.0 / 3)
    for call in calls:
        if any(
            host in call["url"]
            for host in ("dead-active.example", "dead-one.example", "live-two.example")
        ):
            assert call["timeout"] == expected

    group_ids = [g.get("provider_id") for g in result.get("groups", [])]
    assert "custom:live-two" in group_ids


def test_unbounded_budget_keeps_the_historical_cap(monkeypatch, isolate_models_catalog_state):
    monkeypatch.setattr(cfg, "_LIVE_REBUILD_BUDGET_SECONDS", 0.0, raising=False)
    cfg.cfg = {
        "model": {"provider": "custom", "base_url": "http://dead.example/v1"},
        "providers": {},
        "fallback_providers": [],
        "custom_providers": [
            {
                "name": "My Gateway",
                "base_url": "https://api.example.com/v1",
                "api_key": "gw-key",
            }
        ],
    }

    def handler(url, timeout):
        if "api.example.com" in url:
            return _FakeResponse({"data": [{"id": "gw", "name": "GW"}]})
        raise OSError("unreachable")

    calls = _install_urlopen(monkeypatch, handler)
    cfg.get_available_models(force_refresh=True)
    custom_calls = [
        c
        for c in calls
        if "dead.example" in c["url"] or "api.example.com" in c["url"]
    ]
    assert custom_calls
    for call in custom_calls:
        assert call["timeout"] == pytest.approx(cfg.CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS)
