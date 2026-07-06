# Agent instructions for Hermes WebUI

This file is the shared entry point for AI assistants working in this
repository. Keep it project-specific and safe to publish. Do not put personal
machine setup, private network details, credentials, tokens, or local-only
workflow notes here.

## Read first

Before making changes, read:

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/CONTRACTS.md`
4. `CHANGELOG.md`
5. `docs/agent-knowledge.md` for the repository map: architecture, state
   layers, test harness behavior, and investigation checklist
6. `docs/agent-cheat-sheet.md` for a one-page scannable reference: file
   ownership, quick commands, state layers, critical rules, and contract
   routing by subsystem

For architecture, testing, or setup work, also read the matching reference:

- `docs/agent-knowledge.md` for a single onboarding map (start here for
  exploration or pre-integration work)
- `docs/agent-cheat-sheet.md` for a quick lookup while editing
- `ARCHITECTURE.md` for design constraints and current module layout
- `TESTING.md` for local verification commands and manual test guidance
- `docs/onboarding.md` for first-run onboarding behavior
- `docs/troubleshooting.md` for diagnostic flows
- `docs/rfcs/README.md` for larger RFCs and state/durability contracts

For UI or UX work, read `docs/UIUX-GUIDE.md` and `DESIGN.md` before
changing layout, interaction flow, themes, chat rendering, or composer chrome.

## Onboarding and reinstall support

If the task involves install, reinstall, bootstrap, first-run onboarding,
provider setup, local model server setup, Docker onboarding, WSL onboarding, or
support for a failed first run, read `docs/onboarding-agent-checklist.md`
before running commands or inspecting logs.

Follow that checklist's safety rules:

- use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for trials unless the
  human explicitly asks to use real state
- do not delete or overwrite a real `~/.hermes` directory without explicit
  approval
- do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes
- collect non-secret status and log evidence before recommending a fix

## Contribution style

- Keep one logical change per PR; split unrelated refactors or cleanup.
- Read `docs/CONTRACTS.md` and the linked contract/RFC for the touched
  subsystem before editing.
- For local pytest runs, use `./scripts/test.sh` instead of bare `python3`,
  `python -m pytest`, or `pytest`. The script creates/uses the repo `.venv`,
  pins execution to Python 3.11-3.13, and installs missing dev test dependencies.
  `HERMES_WEBUI_TEST_PYTHON` selects the supported base interpreter used to
  create or rebuild `.venv`; it must not install test dependencies into a
  system/Homebrew interpreter directly.
  If a direct pytest invocation reports an unsupported interpreter, rerun through
  `./scripts/test.sh` before debugging product code.
- Prefer the existing Python + vanilla JavaScript structure. Do not add
  dependencies, build tools, frameworks, or long-lived processes without clear
  justification and a rollback story.
- Update docs when changing setup, onboarding, runtime behavior, architecture,
  testing guidance, or user-facing workflows.
- Do not edit `CHANGELOG.md` in ordinary contributor PRs. The release workflow
  owns changelog updates through release commits. If a change is release-note
  worthy, include concise release-note wording in the PR body instead.
- For UI or UX changes, include before/after evidence and test relevant
  desktop, narrow, and mobile states.
- For behavior changes, add or update automated tests where practical and list
  the manual verification performed.
- For runtime, streaming, recovery, replay, compression, or sidebar metadata
  changes, name the state layer being mutated and prove the relevant invariant.
- For Docker build changes in `docker_init.bash`, mirror directory exclusions
  in both the `rsync` and `cp -a` paths — `/opt/hermes` may contain subdirectories
  with restricted permissions (e.g. `.playwright/`).

## Local state and secrets

Hermes WebUI can read and write real agent state, sessions, workspaces,
credentials, and cron data. Treat local validation as potentially destructive
unless you have confirmed the active state directories.

Prefer isolated trial state for experiments:

```bash
HERMES_HOME=/tmp/hermes-webui-agent-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-agent-state \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

Do not include private machine instructions in this tracked file. Use a
git-ignored local note for personal workflow details.

## Cursor Cloud specific instructions

The startup update script provisions the repo-local `.venv` (from
`requirements-dev.txt`) and `node_modules` (ESLint runtime guard). After startup
you can run tests/lint directly against those.

- **Tests:** run through `./scripts/test.sh` (see `README.md` > "Running tests").
  It reuses the pre-provisioned `.venv`.
- **Git-signing gotcha (important):** the full suite occasionally fails with
  `git commit ... timed out after 20 seconds` in `tests/test_workspace_git.py`.
  This is **not** a code bug — those tests spawn `git` with the ambient
  environment, and the Cloud VM's global git config enables `commit.gpgsign`
  (SSH signing helper) plus `core.fsmonitor`, which makes `git commit` in the
  many temp repos slow/hang. Run the suite (or that file) with a
  signing-disabled global config to keep it deterministic, e.g.:

  ```bash
  printf '[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n[core]\n\tfsmonitor = false\n\tuntrackedcache = false\n' > /tmp/git-clean-config
  GIT_CONFIG_GLOBAL=/tmp/git-clean-config ./scripts/test.sh
  ```

  Do not globally disable `commit.gpgsign` — the agent's own commits are signed;
  scope the override to the test run only.
- **Lint:** ruff gate `python3 scripts/ruff_lint.py --diff origin/master` (whole
  tree `--all` is informational backlog per `#3273`); ESLint runtime guard
  `npx eslint --no-config-lookup -c eslint.runtime-guard.config.mjs "static/**/*.js"`.
  `scripts/scope_undef_gate.py` needs an `eslint` binary on `PATH` (else it
  self-skips) — run it as `PATH="$PWD/node_modules/.bin:$PATH" python3 scripts/scope_undef_gate.py`.
- **Running the app:** `bootstrap.py` tries to auto-install the external Hermes
  Agent, which is not present here. To run the WebUI itself against isolated
  state without the agent, launch `server.py` directly with the `.venv` Python:

  ```bash
  HERMES_HOME=/tmp/hermes-home HERMES_WEBUI_STATE_DIR=/tmp/hermes-state \
  HERMES_WEBUI_DEFAULT_WORKSPACE=/tmp/hermes-workspace HERMES_WEBUI_PORT=8787 \
  .venv/bin/python server.py
  ```

  Health check: `curl http://127.0.0.1:8787/health`. In this agent-free mode the
  UI, session management, and workspace file browser work; **chat is disabled**
  because it needs the in-process Hermes Agent plus a configured LLM provider
  API key (neither is available by default).
- **CI on personal forks:** GitHub-hosted workflows are gated to the canonical
  `nesquena/hermes-webui` repo (where Actions billing lives). Fork-local PRs
  skip those jobs instead of failing when the fork account has no Actions quota.
  For full CI signal, open a cross-fork PR to `nesquena/hermes-webui:master`.
  **Note:** a merged fork PR can keep red check rows from commits that ran
  before this skip gate existed; that history cannot be rewritten — verify
  `master` (or a new PR) instead.
