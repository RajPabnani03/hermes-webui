"""Regression tests for Issue #5473: switching workspaces mid-conversation
should start a new chat after confirming, instead of silently rebinding the
existing conversation to the new workspace.

Behavior matrix:
- Same workspace selection: no-op refresh (no dialog, no API call).
- Blank page (no session): auto-create a session bound to the target workspace,
  no prompt, mirroring the existing Opus Q6 fix.
- Different workspace with an active conversation (messages or composer draft):
  show a confirmation dialog with "Keep current chat" as the safe default and
  "Start new chat" as the confirm action. Confirming calls newSession() with the
  target workspace set as the one-shot workspace switch flag.
- Busy / dirty guards still run before the dialog, as in the original code.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")


def _extract_async_function(source: str, name: str) -> str:
    marker = f"async function {name}("
    start = source.find(marker)
    if start < 0:
        marker = f"function {name}("
        start = source.find(marker)
    assert start >= 0, f"{name}() function must exist"
    brace = source.find("{", source.find(")", start))
    assert brace > start, f"{name}() function body must start"
    depth = 0
    in_string = None
    escaped = False
    in_line_comment = False
    in_block_comment = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        nxt = source[idx + 1] if idx + 1 < len(source) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"could not extract {name}()")


def test_switch_to_workspace_same_workspace_is_no_op():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    # Early return before any dialog or network call for same workspace.
    assert "S.session.workspace===targetPath" in fn, (
        "switchToWorkspace must early-return for same-workspace selection"
    )
    # Within the same-workspace branch we should refresh and toast without
    # calling /api/session/update.
    branch_start = fn.find("S.session.workspace===targetPath") + len("S.session.workspace===targetPath")
    branch_end = fn.find("if(!S.session){", branch_start)
    same_branch = fn[branch_start:branch_end]
    assert "showToast(t('workspace_switched_to',targetName))" in same_branch, (
        "same-workspace branch must still toast the refresh"
    )
    assert "api('/api/session/update'" not in same_branch, (
        "same-workspace branch must not rebind the session via /api/session/update"
    )


def test_switch_to_workspace_blank_page_auto_creates_session():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    assert "if(!S.session){" in fn, "switchToWorkspace must have a blank-page auto-create path"
    assert "api('/api/session/new'" in fn, (
        "switchToWorkspace must call /api/session/new when S.session is null"
    )


def test_switch_to_workspace_keeps_busy_guard_before_dialog():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    start = fn.find("if(!S.session){")
    busy_guard = fn.find("if(S.busy)")
    assert start != -1 and busy_guard != -1, "required blocks missing"
    # Blank-page auto-create happens before the busy guard, so the guard still
    # protects a just-created session from concurrent mutation.
    assert start < busy_guard, (
        "blank-page auto-create must happen before the busy guard"
    )
    # The busy guard must return before any workspace update/new-chat dialog.
    update_call = fn.find("api('/api/session/update'")
    dialog_call = fn.find("showConfirmDialog({")
    assert busy_guard < update_call or update_call == -1, (
        "busy guard must run before the session update call"
    )
    assert busy_guard < dialog_call or dialog_call == -1, (
        "busy guard must run before the confirmation dialog"
    )


def test_switch_to_workspace_has_new_chat_confirmation_dialog():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    assert "showConfirmDialog({" in fn, (
        "switchToWorkspace must show a confirmation dialog for active conversations"
    )
    dialog_block = fn[fn.find("showConfirmDialog({") :]
    # Keep-current is the safe default (cancelLabel).
    assert "cancelLabel:t('workspace_switch_keep_current')" in dialog_block, (
        "confirmation dialog must offer 'Keep current chat' as the cancel/safe option"
    )
    # Start-new chat is the confirm action.
    assert "confirmLabel:t('workspace_switch_new_chat_confirm')" in dialog_block, (
        "confirmation dialog must offer 'Start new chat' as the confirm action"
    )
    # Focus the cancel button by default so Enter/Space doesn't accidentally start
    # a new chat (#5473 acceptance requirement).
    assert "focusCancel:true" in dialog_block, (
        "confirmation dialog must focus the cancel button by default"
    )


def test_switch_to_workspace_start_new_chat_uses_new_session():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    start = fn.find("showConfirmDialog({")
    dialog_block = fn[start:]
    # After confirming, set the one-shot workspace switch flag and call newSession.
    assert "S._profileSwitchWorkspace=targetPath" in dialog_block, (
        "confirming workspace switch must set S._profileSwitchWorkspace to the target"
    )
    assert "newSession(false,{awaitWorkspaceLoad:true})" in dialog_block, (
        "confirming workspace switch must start a new chat via newSession()"
    )
    assert "renderSessionList" in dialog_block, (
        "new chat path must refresh the session list sidebar"
    )


def test_switch_to_workspace_rebind_path_only_for_blank_session():
    fn = _extract_async_function(PANELS_JS, "switchToWorkspace")
    # The legacy /api/session/update rebind path must still exist for the case
    # where a session exists but has no conversation (blank session).
    assert "api('/api/session/update'" in fn, (
        "switchToWorkspace must retain /api/session/update rebind for blank sessions"
    )
    # _workspaceSwitchHasConversation must guard the rebind path.
    assert "_workspaceSwitchHasConversation" in fn, (
        "switchToWorkspace must call _workspaceSwitchHasConversation to decide dialog vs rebind"
    )


def test_workspace_switch_has_conversation_helper():
    fn = _extract_async_function(PANELS_JS, "_workspaceSwitchHasConversation")
    # Non-empty messages count as a conversation.
    assert "S.messages.length>0" in fn, (
        "_workspaceSwitchHasConversation must treat non-empty S.messages as a conversation"
    )
    # Non-empty composer draft also counts as a conversation.
    assert "document.getElementById('msg')" in fn or "_composerTextWithPendingSelections" in fn, (
        "_workspaceSwitchHasConversation must check the composer draft"
    )


def test_i18n_keys_for_workspace_switch_confirmation():
    # English bundle must have the new keys.
    assert "workspace_switch_new_chat_title:" in I18N_JS
    assert "workspace_switch_new_chat_message:" in I18N_JS
    assert "workspace_switch_new_chat_confirm:" in I18N_JS
    assert "workspace_switch_keep_current:" in I18N_JS


def test_new_session_inherits_one_shot_workspace_switch():
    """Existing contract: newSession() consumes S._profileSwitchWorkspace so the
    confirmation-set target workspace is honored when starting the new chat."""
    fn = _extract_async_function(SESSIONS_JS, "newSession")
    assert "S._profileSwitchWorkspace" in fn, (
        "newSession must read the one-shot workspace switch flag"
    )
    assert "S._profileSwitchWorkspace=null" in fn or "S._profileSwitchWorkspace = null" in fn, (
        "newSession must consume the one-shot workspace switch flag"
    )
