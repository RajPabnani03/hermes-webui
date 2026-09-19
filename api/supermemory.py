"""Hermes WebUI native Supermemory provider wiring.

Items 1 + 5 + 7: enable the existing native Hermes Supermemory memory
provider via its official config path, map each Hermes profile to its own
valid container tag (``hermes:{profile}``), and enforce strict
profile-aligned tenant isolation with no cross-tag queries.

Official provider path (see Supermemory Docs ``/integrations/hermes``):

* ``pip install supermemory``
* ``hermes memory setup`` (select ``supermemory``, paste ``SUPERMEMORY_API_KEY``)
* or manually: ``hermes config set memory.provider supermemory`` plus
  ``SUPERMEMORY_API_KEY`` in ``$HERMES_HOME/.env`` and
  ``$HERMES_HOME/supermemory.json`` with ``container_tag``.

WebUI owns only the same files the CLI owns for the same profile:

* ``config.yaml`` key ``memory.provider: supermemory`` (exclusive-plugin
  activation, consistent with ``memory/noema`` handling in ``api/routes.py``).
* ``supermemory.json`` key ``container_tag`` (plus preserved native keys
  such as ``auto_recall``/``auto_capture`` when present).
* ``SUPERMEMORY_API_KEY`` read from process/thread-local env or the active
  profile ``.env``. WebUI never accepts, stores, logs, or returns the key
  value -- only a configured boolean.

Container-tag rules (see ``/concepts/container-tags``):

* singular JSON-body ``containerTag`` only (plural ``containerTags`` is
  deprecated and ``/v4`` accepts only the singular form).
* ``^[a-zA-Z0-9_:-]{1,100}$`` enforced on every tag WebUI writes or sends.
* ``containerTag`` is the hard tenant boundary. Session/workspace
  organization belongs in metadata within that tenant, not as extra tags.
  WebUI never issues cross-tag queries and never reuses one profile's tag
  for another profile.

Profile mapping:

* root/default with no isolation opt-in keeps the legacy shared ``hermes``
  tag (single-profile backward compatibility).
* any named profile, or any profile when
  ``HERMES_WEBUI_ISOLATED_PROFILE`` pins a profile directory, maps to
  ``hermes:{profile}`` after sanitization.
* ``SUPERMEMORY_CONTAINER_TAG`` env override is surfaced in status but must
  equal the expected profile tag when isolation is required; otherwise
  status reports ``isolation_ok: False`` (the native provider would
  otherwise collapse tenants at runtime).

Local ``MEMORY.md``/``USER.md``/``SOUL.md`` behavior is unchanged:
Supermemory augments, never replaces, the built-in file memory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SUPERMEMORY_PROVIDER_SLUG = "supermemory"
SUPERMEMORY_API_KEY_ENV = "SUPERMEMORY_API_KEY"
SUPERMEMORY_CONTAINER_TAG_ENV = "SUPERMEMORY_CONTAINER_TAG"
SUPERMEMORY_SHARED_CONTAINER_TAG = "hermes"
SUPERMEMORY_CONFIG_FILENAME = "supermemory.json"
SUPERMEMORY_API_BASE = "https://api.supermemory.ai"
SUPERMEMORY_PROBE_TIMEOUT_SECONDS = 8.0

_TAG_RE = re.compile(r"^[a-zA-Z0-9_:-]{1,100}$")
_IDENTITY_MAX_LEN = 80

_ONLY_SUPERMEMORY_SLUGS = frozenset({SUPERMEMORY_PROVIDER_SLUG})


def is_valid_container_tag(tag: object) -> bool:
    """True when *tag* satisfies the Supermemory naming rules."""
    if not isinstance(tag, str):
        return False
    return bool(_TAG_RE.fullmatch(tag))


def sanitize_profile_identity(raw: object) -> str:
    """Return a safe container-tag identity fragment for *raw*.

    Profile-derived tags are sanitized (invalid chars become ``-``);
    explicit custom tags are validated elsewhere and rejected when invalid.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return "default"
    cleaned = re.sub(r"[^a-z0-9_:-]", "-", text)
    cleaned = cleaned.strip("-_:")
    if not cleaned:
        return "default"
    if len(cleaned) > _IDENTITY_MAX_LEN:
        cleaned = cleaned[:_IDENTITY_MAX_LEN].rstrip("-_:") or "default"
    return cleaned


def _is_root_profile_name(name: str) -> bool:
    try:
        from api.profiles import _is_root_profile as _upstream_is_root

        return bool(_upstream_is_root(name))
    except Exception:
        return (name or "") in ("", "default")


def _active_profile_name() -> str:
    try:
        from api.profiles import get_active_profile_name

        return str(get_active_profile_name() or "").strip()
    except Exception:
        return "default"


def _is_isolated_mode() -> bool:
    try:
        from api.profiles import _is_isolated_profile_mode

        return bool(_is_isolated_profile_mode())
    except Exception:
        return False


def expected_container_tag_for_profile(
    profile_name: object = None, *, isolated: bool | None = None
) -> str:
    """Return the deterministic profile-aligned tag for *profile_name*.

    Root/default without isolation keeps legacy ``hermes``; every other
    case returns ``hermes:{sanitized-profile}``.
    """
    name = str(profile_name or "").strip() if profile_name is not None else _active_profile_name()
    use_isolated = bool(_is_isolated_mode()) if isolated is None else bool(isolated)
    if not name:
        if use_isolated:
            return f"hermes:{sanitize_profile_identity('default')}"
        return SUPERMEMORY_SHARED_CONTAINER_TAG
    if _is_root_profile_name(name) and not use_isolated:
        return SUPERMEMORY_SHARED_CONTAINER_TAG
    return f"hermes:{sanitize_profile_identity(name)}"


def resolve_supermemory_container_tag(
    profile_name: object = None, *, isolated: bool | None = None
) -> str:
    """Resolve the authoritative single tag for a profile (no cross-tag use)."""
    return expected_container_tag_for_profile(profile_name, isolated=isolated)


def build_container_tag_payload(container_tag: object) -> dict:
    """Build a ``{"containerTag": tag}`` body fragment with validation.

    Raises ``ValueError`` for invalid tags. Never emits the deprecated
    plural ``containerTags`` field.
    """
    tag = str(container_tag or "").strip()
    if not is_valid_container_tag(tag):
        raise ValueError(
            "Invalid containerTag: must match ^[a-zA-Z0-9_:-]{1,100}$"
        )
    return {"containerTag": tag}


def get_supermemory_config_path(profile_home: Path | str | None = None) -> Path:
    """Return ``$HERMES_HOME/supermemory.json`` for a profile home."""
    if profile_home is not None:
        return Path(profile_home).expanduser() / SUPERMEMORY_CONFIG_FILENAME
    try:
        from api.profiles import get_active_hermes_home

        return get_active_hermes_home() / SUPERMEMORY_CONFIG_FILENAME
    except Exception:
        base = os.getenv("HERMES_HOME", str(Path.home() / ".hermes")).strip()
        return Path(base or str(Path.home() / ".hermes")).expanduser() / SUPERMEMORY_CONFIG_FILENAME


def _read_supermemory_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_supermemory_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _thread_env_value(name: str) -> str:
    try:
        from api.config import _thread_local_env_value as _tls_env

        return str(_tls_env(name) or "")
    except Exception:
        return str(os.getenv(name) or "")


def _read_dotenv_value(env_path: Path, key: str) -> str:
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip() == key:
            return v.strip().strip("\"'").strip()
    return ""


def _profile_env_path(profile_home: Path | None) -> Path:
    if profile_home is not None:
        return Path(profile_home).expanduser() / ".env"
    try:
        from api.profiles import get_active_hermes_home

        return get_active_hermes_home() / ".env"
    except Exception:
        base = os.getenv("HERMES_HOME", str(Path.home() / ".hermes")).strip()
        return Path(base or str(Path.home() / ".hermes")).expanduser() / ".env"


def is_supermemory_key_configured(profile_home: Path | str | None = None) -> bool:
    """True when ``SUPERMEMORY_API_KEY`` is present (value never returned)."""
    if str(_thread_env_value(SUPERMEMORY_API_KEY_ENV) or "").strip():
        return True
    home = Path(profile_home).expanduser() if profile_home is not None else None
    if str(_read_dotenv_value(_profile_env_path(home), SUPERMEMORY_API_KEY_ENV) or "").strip():
        return True
    return False


def _api_key(profile_home: Path | str | None = None) -> str | None:
    """Return the key material for server-side Bearer auth only (never log)."""
    live = str(_thread_env_value(SUPERMEMORY_API_KEY_ENV) or "").strip()
    if live:
        return live
    home = Path(profile_home).expanduser() if profile_home is not None else None
    file_value = str(_read_dotenv_value(_profile_env_path(home), SUPERMEMORY_API_KEY_ENV) or "").strip()
    return file_value or None


def env_container_tag_override() -> str:
    """Return the valid ``SUPERMEMORY_CONTAINER_TAG`` env override or ``""``."""
    raw = str(_thread_env_value(SUPERMEMORY_CONTAINER_TAG_ENV) or "").strip()
    return raw if is_valid_container_tag(raw) else ""


def get_memory_provider(profile_home: Path | str | None = None) -> str:
    """Return the lowercase ``memory.provider`` for a profile home."""
    try:
        if profile_home is not None:
            from api.config import get_config_for_profile_home

            cfg = get_config_for_profile_home(profile_home) or {}
        else:
            from api.config import get_config

            cfg = get_config() or {}
    except Exception:
        return ""
    mem = cfg.get("memory") if isinstance(cfg, dict) else None
    if not isinstance(mem, dict):
        return ""
    return str(mem.get("provider") or "").strip().lower()


def set_memory_provider(
    provider: object, *, profile_home: Path | str | None = None
) -> str:
    """Persist ``memory.provider: supermemory`` for a profile home.

    Only the native ``supermemory`` slug is accepted; anything else raises
    ``ValueError`` so the exclusive-plugin category can never be pointed at
    an unknown provider through this path.
    """
    slug = str(provider or "").strip().lower()
    if slug not in _ONLY_SUPERMEMORY_SLUGS:
        raise ValueError("Only the 'supermemory' memory provider is supported here")
    from api.config import (
        _get_config_path,
        _load_yaml_config_file,
        _save_yaml_config_file,
        reload_config,
    )

    if profile_home is not None:
        from api.config import _load_yaml_config_file as _load, _save_yaml_config_file as _save

        config_path = Path(profile_home).expanduser() / "config.yaml"
        data = _load(config_path)
        mem = data.get("memory")
        if not isinstance(mem, dict):
            mem = {}
        mem["provider"] = SUPERMEMORY_PROVIDER_SLUG
        data["memory"] = mem
        _save(config_path, data)
        try:
            reload_config()
        except Exception:
            logger.debug("supermemory config reload skipped", exc_info=True)
        return slug
    config_path = _get_config_path()
    data = _load_yaml_config_file(config_path)
    mem = data.get("memory")
    if not isinstance(mem, dict):
        mem = {}
    mem["provider"] = SUPERMEMORY_PROVIDER_SLUG
    data["memory"] = mem
    _save_yaml_config_file(config_path, data)
    try:
        reload_config()
    except Exception:
        logger.debug("supermemory config reload skipped", exc_info=True)
    return slug


def _collect_all_profile_tags(exclude_home: Path | None = None) -> dict[str, str]:
    """Map profile display name -> stored container tag across all homes.

    Used to detect cross-profile tag reuse. Never includes key material.
    """
    found: dict[str, str] = {}
    try:
        from api.profiles import _resolve_base_hermes_home

        base = _resolve_base_hermes_home()
    except Exception:
        base = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    candidates: list[tuple[str, Path]] = [("default", base)]
    profiles_root = base / "profiles"
    try:
        if profiles_root.is_dir():
            for child in sorted(profiles_root.iterdir()):
                if child.is_dir():
                    candidates.append((child.name, child))
    except OSError:
        pass
    for name, home in candidates:
        try:
            if exclude_home is not None and Path(home).resolve() == Path(exclude_home).resolve():
                continue
        except OSError:
            pass
        stored = str((_read_supermemory_json(home / SUPERMEMORY_CONFIG_FILENAME).get("container_tag") or "")).strip()
        if stored and is_valid_container_tag(stored):
            found[name] = stored
    return found


def find_cross_profile_tag_reuse(
    *, profile_home: Path | str | None = None, profile_name: object = None
) -> list[dict]:
    """Return collisions where another profile already uses this tag.

    Empty list means no reuse detected.
    """
    if profile_home is not None:
        home = Path(profile_home).expanduser()
        name = str(profile_name or home.name or "default")
    else:
        try:
            from api.profiles import get_active_hermes_home

            home = get_active_hermes_home()
        except Exception:
            home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
        name = str(profile_name or _active_profile_name() or "default")
    expected = expected_container_tag_for_profile(name)
    others = _collect_all_profile_tags(exclude_home=home)
    collisions = [
        {"profile": other, "container_tag": tag}
        for other, tag in sorted(others.items())
        if tag == expected
    ]
    return collisions


def ensure_profile_scoped_supermemory_config(
    *, profile_home: Path | str | None = None, profile_name: object = None
) -> dict:
    """Ensure a profile home has its own profile-aligned ``supermemory.json``.

    Strict isolation: when a scoped tag is required (named profile or
    isolated mode), a missing/invalid/shared tag is replaced with the
    expected ``hermes:{profile}`` tag, and any valid custom tag that
    collides with another profile's tag is also replaced. Existing native
    keys (``auto_recall``, ``search_mode``, ...) are preserved; credentials
    are never written here.
    """
    if profile_home is not None:
        home = Path(profile_home).expanduser()
        name = str(profile_name or home.name or "default")
        isolated = _is_isolated_mode()
    else:
        try:
            from api.profiles import get_active_hermes_home

            home = get_active_hermes_home()
        except Exception:
            home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
        name = str(profile_name or _active_profile_name() or home.name or "default")
        isolated = _is_isolated_mode()
    expected = expected_container_tag_for_profile(name, isolated=isolated)
    scoped_required = expected != SUPERMEMORY_SHARED_CONTAINER_TAG
    path = home / SUPERMEMORY_CONFIG_FILENAME
    existing = _read_supermemory_json(path)
    stored = str(existing.get("container_tag") or "").strip()
    stored_valid = is_valid_container_tag(stored)
    others = _collect_all_profile_tags(exclude_home=home)
    other_tags = set(others.values())

    needs_write = False
    if not stored_valid:
        needs_write = True
    elif scoped_required and stored == SUPERMEMORY_SHARED_CONTAINER_TAG:
        needs_write = True
    elif scoped_required and stored != expected and stored in other_tags:
        needs_write = True
    elif scoped_required and stored != expected:
        # A unique valid custom tag is preserved only when it cannot be
        # confused with another profile's deterministic tag. Any custom tag
        # that equals a different profile's expected tag is a reuse attempt.
        needs_write = False
        try:
            for other in others:
                if stored == expected_container_tag_for_profile(other):
                    needs_write = True
                    break
        except Exception:
            needs_write = True
    elif not scoped_required and stored_valid:
        needs_write = False

    replaced = False
    if needs_write:
        data = dict(existing) if isinstance(existing, dict) else {}
        data["container_tag"] = expected
        _write_supermemory_json(path, data)
        replaced = True
        stored = expected
    else:
        # Normalize permissions even when the tag already matches.
        try:
            if path.exists():
                os.chmod(path, 0o600)
        except OSError:
            pass
    env_override = env_container_tag_override()
    return {
        "path": str(path),
        "container_tag": stored,
        "expected_container_tag": expected,
        "profile": name,
        "isolated": bool(isolated),
        "scoped_required": bool(scoped_required),
        "replaced": bool(replaced),
        "env_override_tag": env_override,
        "env_override_conflict": bool(env_override and scoped_required and env_override != expected),
        "cross_profile_collisions": find_cross_profile_tag_reuse(
            profile_home=home, profile_name=name
        ),
    }


def get_memory_provider_status(
    *, profile_home: Path | str | None = None, profile_name: object = None
) -> dict:
    """Return provider status without ever exposing key material."""
    if profile_home is not None:
        home = Path(profile_home).expanduser()
        name = str(profile_name or home.name or "default")
    else:
        try:
            from api.profiles import get_active_hermes_home

            home = get_active_hermes_home()
        except Exception:
            home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
        name = str(profile_name or _active_profile_name() or home.name or "default")
    isolated = _is_isolated_mode()
    expected = expected_container_tag_for_profile(name, isolated=isolated)
    scoped_required = expected != SUPERMEMORY_SHARED_CONTAINER_TAG
    provider = get_memory_provider(home)
    stored = str((_read_supermemory_json(home / SUPERMEMORY_CONFIG_FILENAME).get("container_tag") or "")).strip()
    stored_valid = is_valid_container_tag(stored)
    env_override = env_container_tag_override()
    if scoped_required:
        effective = expected
    else:
        effective = stored if stored_valid else expected
    if env_override and scoped_required and env_override != expected:
        isolation_ok = False
    elif scoped_required:
        isolation_ok = effective == expected and not find_cross_profile_tag_reuse(
            profile_home=home, profile_name=name
        )
    else:
        isolation_ok = True
    return {
        "provider": provider,
        "enabled": provider == SUPERMEMORY_PROVIDER_SLUG,
        "key_configured": bool(is_supermemory_key_configured(home)),
        "container_tag": effective,
        "expected_container_tag": expected,
        "stored_container_tag": stored if stored_valid else "",
        "env_override_tag": env_override,
        "profile": name,
        "isolated": bool(isolated),
        "scoped_required": bool(scoped_required),
        "isolation_ok": bool(isolation_ok),
        "supermemory_json": str(home / SUPERMEMORY_CONFIG_FILENAME),
        "api_base": SUPERMEMORY_API_BASE,
    }


def _server_request(
    *, method: str, path: str, api_key: str, payload: dict | None
) -> tuple[int, dict]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        SUPERMEMORY_API_BASE + path,
        data=body if method != "GET" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=SUPERMEMORY_PROBE_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        status = int(exc.code or 500)
    except Exception as exc:
        raise RuntimeError(f"supermemory request failed: {type(exc).__name__}") from exc
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {"raw": raw[:2000]}
    return status, data if isinstance(data, dict) else {"raw": str(data)[:2000]}


def probe_container_access(
    *, profile_home: Path | str | None = None, profile_name: object = None
) -> dict | None:
    """Non-sensitive server-side access probe for the profile's own tag.

    Reads container settings for exactly the active profile's tag via
    ``GET /v3/container-tags/{tag}``. Returns ``None`` when the provider is
    disabled, the key is absent, or the request fails, so ordinary WebUI
    paths stay available. The key value is used only as a Bearer header and
    is never logged or returned. Raises ``ValueError`` if asked to probe a
    tag that is not the active profile's own tag (cross-tag guard).
    """
    status = get_memory_provider_status(profile_home=profile_home, profile_name=profile_name)
    if not status["enabled"] or not status["key_configured"]:
        return None
    tag = str(status["container_tag"] or "")
    if not is_valid_container_tag(tag):
        return None
    if tag != str(status["expected_container_tag"] or "") and bool(status["scoped_required"]):
        raise ValueError("Refusing cross-profile container probe")
    key = _api_key(profile_home)
    if not key:
        return None
    safe_path = "/v3/container-tags/" + tag
    try:
        http_status, data = _server_request(
            method="GET", path=safe_path, api_key=key, payload=None
        )
    except Exception:
        logger.debug("supermemory container probe failed", exc_info=True)
        return None
    return {"http_status": http_status, "container_tag": tag, "ok": http_status < 400, "data": data}


def search_own_container(
    query: str,
    *,
    profile_home: Path | str | None = None,
    profile_name: object = None,
    limit: int = 5,
) -> dict | None:
    """Server-side semantic search scoped strictly to the profile's own tag.

    Uses singular ``containerTag`` per the current API. Never accepts a
    foreign tag argument, so cross-profile queries are structurally
    impossible through this helper.
    """
    status = get_memory_provider_status(profile_home=profile_home, profile_name=profile_name)
    if not status["enabled"] or not status["key_configured"]:
        return None
    tag = str(status["container_tag"] or "")
    if not is_valid_container_tag(tag):
        return None
    text = str(query or "").strip()
    if not text:
        raise ValueError("query is required")
    try:
        count = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        count = 5
    key = _api_key(profile_home)
    if not key:
        return None
    payload = {"q": text[:2000], **build_container_tag_payload(tag), "limit": count}
    try:
        http_status, data = _server_request(
            method="POST", path="/v4/search", api_key=key, payload=payload
        )
    except Exception:
        logger.debug("supermemory scoped search failed", exc_info=True)
        return None
    return {"http_status": http_status, "container_tag": tag, "ok": http_status < 400, "data": data}


__all__ = [
    "SUPERMEMORY_PROVIDER_SLUG",
    "SUPERMEMORY_API_KEY_ENV",
    "SUPERMEMORY_SHARED_CONTAINER_TAG",
    "SUPERMEMORY_CONFIG_FILENAME",
    "SUPERMEMORY_API_BASE",
    "is_valid_container_tag",
    "sanitize_profile_identity",
    "expected_container_tag_for_profile",
    "resolve_supermemory_container_tag",
    "build_container_tag_payload",
    "get_supermemory_config_path",
    "is_supermemory_key_configured",
    "env_container_tag_override",
    "get_memory_provider",
    "set_memory_provider",
    "ensure_profile_scoped_supermemory_config",
    "find_cross_profile_tag_reuse",
    "get_memory_provider_status",
    "probe_container_access",
    "search_own_container",
]
