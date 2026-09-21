# Bugs Backlog

This file tracks UI bugs and polish items. Fixed items are kept for reference.

---

## Open Bugs

(none — #7585 advisor shipped, see Fixed below)

---

## Known Limitations

- **Two-container Docker setup: tools run in WebUI container** — In the two-container setup (hermes-agent + hermes-webui as separate containers), WebUI-initiated agent sessions run tools in the WebUI container, not the agent container. This is a known architectural constraint. Workaround: use the combined single-image approach, or initiate sessions via the CLI in the agent container. (#681)

- **Image-in-chat vs. saved-to-workspace mismatch** — When the agent displays an inline image (from a URL) and the user asks it to save that image, the agent issues a fresh download which may return a different file if the source URL is CDN-rotated or parameterized. The WebUI correctly renders whatever URL the agent provides. Fix requires agent-side URL caching. (#641)

- **MCP tools not available in WebUI sessions** — MCP servers must be configured in the active profile's config.yaml under mcp_servers:. If MCP tools are not appearing, check that the profile is correct and the MCP server process is reachable from inside the WebUI container. (#628)

- **os.environ race condition in concurrent sessions** — Concurrent agent sessions share process-level os.environ for TERMINAL_CWD, HERMES_SESSION_KEY, and HERMES_HOME. _ENV_LOCK serializes mutations but does not fully isolate env vars during agent execution. Upstream fix pending in hermes-agent. (#195)

---

## Fixed

### Historical ambiguous provider pins (#7585) — repair advisor

- **Was:** Already-saved unqualified model/provider pairs could not always be
  repaired automatically: the same model can legitimately be served by Nous,
  OpenRouter, or a custom endpoint, and a missing catalog entry is not proof
  of ownership. The only fix was manual re-selection in the model picker.
- **Fix:** Opening the model picker on a session with a model but no provider
  fetches a read-only suggestion from `POST /api/session/provider-suggestion`
  and shows a one-click Apply banner. A sole catalog candidate is suggested
  deterministically; genuine multi-provider ambiguity goes to a Jev `Choice`
  judgment over session/profile/catalog evidence, labelled "please confirm"
  below 0.70 confidence. No API key or a Jev failure degrades to the previous
  manual flow — chat is never blocked. Apply reuses the existing explicit
  `model_provider` update path, so nothing is ever silently re-pinned and
  existing explicit selections remain authoritative.
- **Verification:** `tests/test_issue7585_provider_repair_advisor.py`
  (sole/multi-candidate, low-confidence flag, no-key/API-error/out-of-set
  degradation, endpoint read-only) plus a live Jev run through
  `_suggest_session_provider`; existing
  `tests/test_issue7585_stale_model_provider.py` unmodified and green.

### Blank reply after replay reconnect (#7640)

- **Was:** A pending user prompt or historical reply could authorize a replay
  cursor even though the browser had no live assistant output for that cursor.
- **Fix:** Restore and direct reattach reset cursor-only caches to replay from
  zero, retaining the pending prompt. Recoverable live output retains its cursor.
- **Verification:** `tests/test_issue7640_7625_recovery.py` and
  `tests/browser_recovery_check.js` cover cursor reset, second reconnect, and
  visible terminal settlement without reload (`done`, `stream_end`, and cancel).

### Unbounded automatic session refreshes (#7625)

- **Was:** Refresh, terminal/cancel recovery, undo, and retry fetched entire
  transcripts and legacy tool-call lists. Compression preflight also fetched
  transcript data just to check session existence.
- **Fix:** Automatic transcript reads request the existing 30-row tail window
  and retain tools, offsets, and truncation metadata. Compression preflight is
  metadata-only. Explicit full-history operations remain supported; the server
  API default is unchanged. Outline full-history jumps keep their absolute target.
- **Verification:** Behavioral client tests assert bounded request parameters,
  preserved history controls, and metadata-only preflight. Synthetic Chromium
  recovery checks passed at desktop, narrow, and mobile-width viewports.

### Repeated identical user turns disappearing (#7587)

- **Was:** Content-only identity collapsed distinct repeated prompts or images
  during sidecar/state.db restore and next-turn context reconstruction.
- **Fix:** User dedup now requires matching finite timestamps and private
  identities. Ambiguous occurrences are preserved; exact mirrors still dedup.
  Bounded prefix reads carry timestamps, and context alignment does not skip
  an unmatched user turn to find a later assistant mirror.
- **Tradeoff:** Historical mirrors without matching occurrence metadata may
  appear twice. Preserving input takes precedence over speculative suppression.
- **Verification:** `tests/test_issue7587_repeated_user_turns.py` and HTTP
  restore coverage in `tests/test_webui_state_db_reconciliation.py`.

### Provider inherited across model changes — prevention for #7585

- **Was:** A model-only update or chat request retained the old model's
  `model_provider`, potentially routing the new model through OpenRouter.
- **Fix:** Session updates, streaming/synchronous chat, and goal kickoff only
  inherit a provider when the model is unchanged. An explicitly supplied provider
  remains authoritative. Updates persist the corrected pair using the existing
  session save path; no bulk migration or catalog-driven rerouting is performed.
- **Verification:** `tests/test_issue7585_stale_model_provider.py` exercises
  model changes, unchanged selections, explicit custom providers, route save
  behavior, and profile-aware chat startup.

### ~~Session title truncation / hover actions~~ -- Fixed (Sprint 16)

- **Was:** Action icons reserved ~30px of space even when invisible, truncating titles.
- **Fix:** Wrapped all action buttons in a `.session-actions` overlay container with `position:absolute`. Titles now use full available width. Actions appear on hover with a gradient fade from the right edge.

### ~~Folder/project assignment interaction feels sticky~~ -- Fixed (Sprint 16)

- **Was:** Folder icon stayed permanently visible (blue, 60% opacity) when a session belonged to a project.
- **Fix:** Replaced `.has-project` persistent button with a colored left border matching the project color. The folder button now only appears in the hover overlay like all other actions.

### ~~Project picker clipping and width~~ -- Fixed (v0.17.3)

- **Was:** Picker was clipped by `overflow:hidden` on `.session-item` ancestors. With `position:fixed`, no containing block constrained width -- picker stretched to full viewport.
- **Fix:** Dynamic width calculation (min 160px, max 220px). Event listener reordering. Cleanup sequence corrected. (PR #25)

### ~~NameError crash in model discovery~~ -- Fixed (v0.17.3)

- **Was:** `logger.debug()` called in custom endpoint `except` block, but `logger` was never imported in `config.py`. Every failed endpoint fetch crashed with `NameError`.
- **Fix:** Replaced with silent `pass` -- unreachable endpoints are expected when no local LLM is configured. (PR #24)

---

## Notes

- Sprint 16 replaced all emoji HTML entities with monochrome SVG line icons (`ICONS` constant in `sessions.js`).
- All session action buttons now use the overlay pattern for consistent UX.
