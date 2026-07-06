"""Regression checks for Issue #5671: workspace switcher a11y and
New Chat screen-reader announcement.

Focus:
- The composer workspace chip exposes itself as a popup button with an
  accessible label reflecting the current workspace.
- syncWorkspaceDisplays() updates the chip's aria-label / aria-expanded
  state dynamically.
- Starting a new chat announces the resolved workspace to a polite live
  region so screen-reader users know where the new conversation landed.
- The required i18n strings exist in English; missing keys fall back to
  English at runtime (this is a known convention, so we only enforce
  the primary English bundle here).
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    marker = re.search(rf"(^|\n)(?:async\s+)?function\s+{re.escape(name)}\(", src)
    assert marker is not None, f"{name}() not found"
    start = marker.start()
    next_marker = re.search(
        r"\n(?:function\s+\w+\(|async\s+function\s+\w+\(|class\s+\w+)",
        src[start + 1 :],
    )
    if next_marker:
        end = start + 1 + next_marker.start()
    else:
        end = len(src)
    return src[start:end]


def test_composer_workspace_chip_has_popup_ar_attributes():
    chip = re.search(
        r'<button[^>]*\bid="composerWorkspaceChip"[^>]*>',
        INDEX_HTML,
        re.DOTALL,
    )
    assert chip, "composerWorkspaceChip button not found in index.html"
    tag = chip.group(0)
    assert 'aria-haspopup="true"' in tag
    assert 'aria-expanded="false"' in tag
    assert 'aria-label="Switch workspace"' in tag

    label = re.search(
        r'<span[^>]*\bid="composerWorkspaceLabel"[^>]*>',
        INDEX_HTML,
        re.DOTALL,
    )
    assert label, "composerWorkspaceLabel span not found"
    assert 'aria-hidden="true"' in label.group(0)


def test_screen_reader_announcement_live_region_markup():
    region = re.search(
        r'<div[^>]*\bid="srAnnouncements"[^>]*>',
        INDEX_HTML,
        re.DOTALL,
    )
    assert region, "srAnnouncements live region not found in index.html"
    tag = region.group(0)
    assert 'aria-live="polite"' in tag
    assert 'aria-atomic="true"' in tag
    assert 'position:absolute' in tag
    assert 'left:-10000px' in tag
    assert 'overflow:hidden' in tag


def test_sync_workspace_displays_updates_chip_accessibility():
    block = _function_block(PANELS_JS, "syncWorkspaceDisplays")
    assert "composerChip.setAttribute('aria-label'" in block
    assert "workspace_switcher_aria_label" in block
    assert "workspace_switcher_no_workspace_aria_label" in block
    assert "composerChip.setAttribute('aria-expanded'" in block
    assert "composerChip.setAttribute('aria-haspopup'" in block


def test_new_session_announces_resolved_workspace():
    block = _function_block(SESSIONS_JS, "newSession")
    assert "_announceNewChatWorkspace(S.session)" in block

    helper = _function_block(SESSIONS_JS, "_announceNewChatWorkspace")
    assert "workspace_new_chat_announcement" in helper
    assert "t('workspace_new_chat_announcement', name)" in helper
    assert "requestAnimationFrame" in helper
    assert "ann.textContent = '';" in helper
    assert "ann.textContent = message;" in helper
    assert "getWorkspaceFriendlyName" in helper


def test_english_i18n_keys_for_workspace_switcher_announcement():
    # The en bundle is the first LOCALES block; key presence in other locales
    # is handled by runtime fallback, so we enforce the canonical English keys.
    assert "workspace_switcher_aria_label:" in I18N_JS
    assert "workspace_switcher_no_workspace_aria_label:" in I18N_JS
    assert "workspace_new_chat_announcement:" in I18N_JS
