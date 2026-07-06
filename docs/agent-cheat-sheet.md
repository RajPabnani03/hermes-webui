# Hermes WebUI — Agent Cheat Sheet

> One-page reference for humans and AI agents working on this repo. Keep it short, scannable, and honest. Update it when you learn a durable rule that other agents will need.
>
> **Read first:** `AGENTS.md` → this file → `docs/agent-knowledge.md` → `ARCHITECTURE.md`.

---

## Stack constraints (do not violate without approval)

- **No build step.** No bundler, no frontend framework, no npm build pipeline.
- **Backend:** Python stdlib HTTP server (`server.py`) + `api/` package.
- **Frontend:** vanilla JS modules in `static/` loaded by `index.html` in dependency order.
- **Hermes Agent:** imported at runtime via `sys.path` (sibling checkout or `~/.hermes/hermes-agent`).
- **Runtime state:** lives outside the repo under `~/.hermes/webui/` by default; use isolated temp dirs for experiments.

---

## File ownership map

| File | Owns | Typical change |
|------|------|----------------|
| `server.py` | Thin HTTP shell, auth middleware, dispatch, TLS | Add a new route endpoint only if it needs server-level plumbing |
| `api/routes.py` | All GET/POST route handlers | Most new API endpoints go here |
| `api/streaming.py` | SSE engine, `AIAgent` invocation, cancel/compress | Streaming, recovery, run-state changes |
| `api/models.py` | Session CRUD, session cache, sidecar/state.db bridge | Session persistence, listing, metadata |
| `api/config.py` | Discovery, env, model/provider catalog | Provider/model detection, env overrides |
| `api/profiles.py` | Multi-profile `HERMES_HOME` switching | Profile switching, profile-scoped paths |
| `api/workspace.py` | File tree, safe path resolution, git badge | Workspace file operations, path security |
| `api/auth.py` | Optional password + signed cookies | Authentication, session security |
| `static/ui.js` | Global `S` state, `api()`, markdown, tool cards, composer chrome | Shared UI helpers, state shape |
| `static/sessions.js` | Sidebar session list, search, projects, recovery | Session list, projects, sidebar actions |
| `static/messages.js` | `send()`, SSE event handlers, approval/clarify, transcript | Chat send/receive, streaming transcript |
| `static/workspace.js` | File browser, preview, file ops | Right panel file interactions |
| `static/panels.js` | Control Center, cron/skills/memory/profiles/settings | Settings and global panels |
| `static/boot.js` | Boot IIFE, mobile nav, voice, theme/skin sync | Global event wiring, theme, mobile layout |
| `static/index.html` | App shell + script load order | Add a new static module or preload data |
| `static/style.css` | All CSS, themes, skins, mobile | Visual changes, responsive layout |
| `tests/conftest.py` | Isolated server fixture, state/proximity guards | Test isolation changes |
| `tests/test_regressions.py` | Permanent regression gate | Add a test for any reintroduced bug class |

---

## One-liner commands

```bash
# Run the full test suite (use this, not bare pytest)
./scripts/test.sh

# Regression gate only — run before/after risky edits
./scripts/test.sh tests/test_regressions.py -q

# Count current tests
./scripts/test.sh tests/ --collect-only -q

# Python diff-scoped lint gate
python3 scripts/ruff_lint.py --diff origin/master

# Static JS runtime guard (catches const reassign / import assign)
npx eslint --no-config-lookup -c eslint.runtime-guard.config.mjs "static/**/*.js"

# Headless browser smoke (loads the UI in Chromium, fails on console errors)
python tests/browser_smoke.py

# Run server agent-free against isolated state
HERMES_HOME=/tmp/hermes-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-state \
HERMES_WEBUI_DEFAULT_WORKSPACE=/tmp/hermes-workspace \
HERMES_WEBUI_PORT=8787 \
.venv/bin/python server.py
```

---

## State layers (name the one you touch)

| Layer | Where | Holds |
|-------|-------|-------|
| Session JSON | `{STATE_DIR}/sessions/{id}.json` | `messages[]`, title, workspace, model, flags |
| Session index | `{STATE_DIR}/sessions/_index.json` | Compact metadata for sidebar list |
| WebUI settings | `{STATE_DIR}/settings.json` | Theme, send key, password hash, toggles |
| Workspaces registry | `{STATE_DIR}/workspaces.json` | Named workspace paths |
| Profile homes | `~/.hermes/profiles/*` or profile-scoped `HERMES_HOME` | `config.yaml`, `.env`, skills |
| Sidecar / state.db | Under state dir | Run metadata, reconciliation |
| Turn journal | Append-only audit | Crash-safe turn lifecycle |
| Browser `localStorage` | Client only | Last session id, `INFLIGHT`, panel widths, theme |

---

## Critical rules (do not regress)

| ID | Rule |
|----|------|
| R1 | `deleteSession()` never calls `newSession()` |
| R2 | `/api/upload` must be handled before `read_body()` in POST dispatch |
| R3 | `run_conversation(..., task_id=…)` not `session_id=` |
| R4 | `stream_delta_callback` may receive `None` sentinel — guard it |
| R5 | `send()` captures `activeSid` before any `await` |
| R6 | Boot must not auto-create a session (only `+` button or first send) |
| R7 | All `SESSIONS` dict access under `LOCK` |
| R8 | No tracebacks in API 500 responses |
| R9 | Approvals iterate `pattern_keys` (plural), not legacy singular only |

---

## Contract routing by subsystem

Before editing, read the relevant contract/RFC:

| Subsystem | Start with |
|-----------|------------|
| Streaming, recovery, replay, compression, run state | `docs/rfcs/webui-run-state-consistency-contract.md` |
| Assistant reply rendering / live vs final | `docs/rfcs/live-to-final-assistant-replies.md` |
| Session routing, URL params, boot restore | `docs/rfcs/canonical-session-resolution.md` |
| Pending inputs during active runs (queue, steer, interrupt) | `docs/rfcs/webui-pending-intent-controls.md` |
| Turn journal / crash-safe writes | `docs/rfcs/turn-journal.md` |
| Run adapter / execution ownership | `docs/rfcs/hermes-run-adapter-contract.md` |
| UI/UX, themes, skins | `DESIGN.md` + `docs/UIUX-GUIDE.md` |
| Onboarding, first run, provider setup | `docs/onboarding.md` + `docs/onboarding-agent-checklist.md` |
| Docker / deployment | `docs/docker.md` |
| Security, auth, path handling | `api/auth.py`, `api/workspace.py`, `docs/EXTENSIONS.md` |

---

## Safety defaults for agents

Use isolated state for experiments unless the human explicitly asks for real state:

```bash
HERMES_HOME=/tmp/hermes-webui-agent-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-agent-state \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

Never print API keys, OAuth tokens, cookies, full `.env` files, full `auth.json` files, or password hashes.

---

## Test naming conventions

| Pattern | Meaning |
|---------|---------|
| `test_sprintN.py` | Sprint-era HTTP integration batch |
| `test_issueNNNN_*.py` | GitHub issue regression pin |
| `test_NNN_*.py` | Issue number shorthand |
| `test_regressions.py` | Permanent gate — run before/after risky changes |
| `test_*_static.py` | Static analysis (no live agent) |

---

## PR body checklist

Every PR should contain:

- `Thinking Path` — why this change
- `What Changed`
- `Why It Matters`
- `Verification` — tests, manual checks, state invariant
- `Risks / Follow-ups`
- `Model Used` — provider/model, or `None — human-authored`
- `Contract Routing` — if a contract/RFC is touched
- `Contract Change` — if a public contract is intentionally changed

UI/UX changes need before/after evidence and responsive-state coverage. Release-note-worthy changes go in the PR body, **not** `CHANGELOG.md`.

---

*Last verified: 2026-07-06 against repo `master` — 11,781 tests collected via `./scripts/test.sh tests/ --collect-only -q`.*
