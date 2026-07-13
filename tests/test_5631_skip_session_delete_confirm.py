"""Regression tests for #5631 skip session delete confirmation toggle."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
SESSIONS_JS_PATH = REPO_ROOT / "static" / "sessions.js"
SESSIONS_JS = SESSIONS_JS_PATH.read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
CONFIG_PY = (REPO_ROOT / "api" / "config.py").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(source: str) -> str:
    result = subprocess.run(
        [NODE],
        input=source,
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def _node_prelude() -> str:
    return f"""
const sessionsSrc = {SESSIONS_JS!r};
function extractFunc(src, name) {{
  const re = new RegExp('(?:async\\\\s+)?function\\\\s+' + name + '\\\\s*\\\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {{
    if (src[i] === '{{') depth++;
    else if (src[i] === '}}') depth--;
    i++;
  }}
  return src.slice(start, i);
}}
function evalSession(name) {{
  globalThis[name] = (0, eval)('(' + extractFunc(sessionsSrc, name) + ')');
}}
"""


def test_default_setting_is_false():
    assert '"skip_session_delete_confirm": False' in CONFIG_PY


def test_preferences_checkbox_exists():
    assert 'id="settingsSkipSessionDeleteConfirm"' in INDEX_HTML
    assert 'settings_label_skip_session_delete_confirm' in INDEX_HTML
    assert 'settings_desc_skip_session_delete_confirm' in INDEX_HTML


def test_helper_never_skips_worktree_sessions():
    source = _node_prelude() + """
global.S = { settings: { skip_session_delete_confirm: true } };
global.localStorage = { getItem: () => '1' };
evalSession('_skipSessionDeleteConfirm');
const plain = _skipSessionDeleteConfirm({ session_id: 'abc' });
const worktree = _skipSessionDeleteConfirm({ session_id: 'def', worktree_path: '/tmp/wt' });
const nullSession = _skipSessionDeleteConfirm(null);
console.log(JSON.stringify({ plain, worktree, nullSession }));
"""
    payload = json.loads(_run_node(source))
    assert payload["plain"] is True
    assert payload["worktree"] is False
    assert payload["nullSession"] is False


def test_helper_requires_localstorage_or_server_setting():
    source = _node_prelude() + """
const cases = [];
function run(name, s, ls) {
  global.S = s;
  global.localStorage = { getItem: () => ls };
  evalSession('_skipSessionDeleteConfirm');
  cases.push({ name, value: _skipSessionDeleteConfirm({ session_id: 'abc' }) });
}
run('localStorage only', { settings: {} }, '1');
run('server setting only', { settings: { skip_session_delete_confirm: true } }, '0');
run('both', { settings: { skip_session_delete_confirm: true } }, '1');
run('neither', { settings: {} }, '0');
console.log(JSON.stringify(cases));
"""
    payload = json.loads(_run_node(source))
    assert payload == [
        {"name": "localStorage only", "value": True},
        {"name": "server setting only", "value": True},
        {"name": "both", "value": True},
        {"name": "neither", "value": False},
    ]


def test_single_delete_skips_confirm_when_setting_enabled():
    # The function should short-circuit the confirm dialog when skip is true.
    assert "const skipConfirm=_skipSessionDeleteConfirm(session);" in SESSIONS_JS
    assert "const ok=skipConfirm?true:await showConfirmDialog({" in SESSIONS_JS


def test_batch_delete_skips_confirm_only_when_no_worktree():
    assert "const anyWorktree=ids.some(sid=>{" in SESSIONS_JS
    assert "const skipConfirm=!anyWorktree && localStorage.getItem('hermes-skip-delete-confirm')==='1';" in SESSIONS_JS
    assert "const ok=skipConfirm?true:await showConfirmDialog({" in SESSIONS_JS
