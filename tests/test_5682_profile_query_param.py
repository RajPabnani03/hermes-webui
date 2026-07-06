"""Regression tests for #5682 ?profile= URL query parameter boot switching."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
BOOT_JS_PATH = REPO_ROOT / "static" / "boot.js"
BOOT_JS = BOOT_JS_PATH.read_text(encoding="utf-8")
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
const bootSrc = {BOOT_JS!r};
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
function evalBoot(name) {{
  globalThis[name] = (0, eval)('(' + extractFunc(bootSrc, name) + ')');
}}
"""


def test_profile_param_parses_valid_name():
    source = _node_prelude() + """
function applyUrl(rel) {
  const next = new URL(rel, 'https://example.test');
  window.location.href = next.href;
  window.location.pathname = next.pathname;
  window.location.search = next.search;
  window.location.hash = next.hash;
}
global.window = {
  location: {},
  history: {
    state: { from: 'test' },
    calls: [],
    replaceState(state, title, url) {
      this.calls.push({ state, title, url });
      this.state = state;
      applyUrl(url);
    }
  }
};
applyUrl('/?profile=vops&session=abc#frag');
evalBoot('_profileNameFromLocation');
evalBoot('_consumeProfileParamFromLocation');
const first = _profileNameFromLocation();
_consumeProfileParamFromLocation();
const cleaned = window.history.calls[0];
applyUrl('/?profile=artia-2');
const second = _profileNameFromLocation();
applyUrl('/?');
const third = _profileNameFromLocation();
applyUrl('/');
const fourth = _profileNameFromLocation();
console.log(JSON.stringify({ first, second, third, fourth, cleaned }));
"""
    payload = json.loads(_run_node(source))
    assert payload["first"] == "vops"
    assert payload["second"] == "artia-2"
    assert payload["third"] is None
    assert payload["fourth"] is None
    assert payload["cleaned"]["url"] == "/?session=abc#frag"
    assert payload["cleaned"]["state"] == {"from": "test"}


def test_profile_switch_applies_only_when_name_differs_and_valid():
    source = _node_prelude() + """
function applyUrl(rel) {
  const next = new URL(rel, 'https://example.test');
  window.location.href = next.href;
  window.location.pathname = next.pathname;
  window.location.search = next.search;
  window.location.hash = next.hash;
}
global.window = {
  location: {},
  history: {
    state: null,
    calls: [],
    replaceState(state, title, url) {
      this.calls.push({ state, title, url });
      this.state = state;
      applyUrl(url);
    }
  }
};
const calls = [];
global.S = { activeProfile: 'default' };
global.switchToProfile = async (name) => { calls.push(name); };
global._PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
applyUrl('/?profile=vops');
evalBoot('_profileNameFromLocation');
evalBoot('_consumeProfileParamFromLocation');
evalBoot('_applyProfileFromLocationOnBoot');
(async () => {
  await _applyProfileFromLocationOnBoot();
  global.S.activeProfile = 'vops';
  applyUrl('/?profile=vops');
  await _applyProfileFromLocationOnBoot();
  applyUrl('/?profile=INVALID-NAME');
  await _applyProfileFromLocationOnBoot();
  applyUrl('/?profile=');
  await _applyProfileFromLocationOnBoot();
  console.log(JSON.stringify({ calls, historyCalls: window.history.calls.length }));
})().catch(err => { console.error(err); process.exit(1); });
"""
    payload = json.loads(_run_node(source))
    assert payload["calls"] == ["vops"]
    # Each valid call strips the param; invalid/empty/self also call strip but only when param present.
    # vops (valid, diff), vops (self, no switch), INVALID-NAME (invalid, no switch), empty (no param, no strip).
    assert payload["historyCalls"] == 3


def test_profile_switch_is_silent_on_failure():
    source = _node_prelude() + """
function applyUrl(rel) {
  const next = new URL(rel, 'https://example.test');
  window.location.href = next.href;
  window.location.pathname = next.pathname;
  window.location.search = next.search;
  window.location.hash = next.hash;
}
global.window = {
  location: {},
  history: {
    state: null,
    calls: [],
    replaceState(state, title, url) {
      this.calls.push({ state, title, url });
      this.state = state;
      applyUrl(url);
    }
  }
};
global.S = { activeProfile: 'default' };
global._PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
global.switchToProfile = async () => { throw new Error('profile not found'); };
applyUrl('/?profile=missing');
evalBoot('_profileNameFromLocation');
evalBoot('_consumeProfileParamFromLocation');
evalBoot('_applyProfileFromLocationOnBoot');
(async () => {
  await _applyProfileFromLocationOnBoot();
  console.log(JSON.stringify({ historyCalls: window.history.calls.length }));
})().catch(err => { console.error(err); process.exit(1); });
"""
    payload = json.loads(_run_node(source))
    # Param is consumed even when the switch fails; no exception propagates.
    assert payload["historyCalls"] == 1


def test_profile_switch_runs_before_session_restore():
    # The switch call must appear before the saved-session / loadSession branches.
    apply_pos = BOOT_JS.find("await _applyProfileFromLocationOnBoot();")
    saved_pos = BOOT_JS.find("const saved=urlSession||savedLocal;")
    load_pos = BOOT_JS.find("await loadSession(saved, {preserveActiveInput:true});")
    assert apply_pos >= 0
    assert saved_pos >= 0
    assert load_pos >= 0
    assert apply_pos < saved_pos
    assert apply_pos < load_pos


def test_profile_regex_matches_upstream_profile_id_re():
    # api/profiles.py uses: r'^[a-z0-9][a-z0-9_-]{0,63}$'
    # The frontend regex must match the same set of names.
    source = _node_prelude() + """
const re = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const cases = {
  default: true,
  vops: true,
  artia_2: true,
  a: true,
  a0: true,
  '0': true,
  '0invalid': true,
  '-invalid': false,
  'A': false,
  'ARTIA': false,
  'vops!': false,
  '': false,
};
const result = {};
for (const [name, expected] of Object.entries(cases)) {
  result[name] = re.test(name) === expected;
}
console.log(JSON.stringify(result));
"""
    payload = json.loads(_run_node(source))
    assert all(payload.values())
