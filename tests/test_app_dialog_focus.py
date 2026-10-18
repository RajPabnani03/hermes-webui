"""Focus lifecycle for the shared confirm/prompt dialog.

Closing used to set aria-hidden and hide the overlay, then restore focus on a
timer. Until that timer fired, the confirm button stayed focused inside a
hidden dialog. A detached opener was also a legal restore target, so focus
never left that hidden control.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _dialog_slice() -> str:
    source = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    start = source.index("// ── Shared app dialogs")
    end = source.index("function _copyText(text)")
    return source[start:end]


def _run(scenario: str) -> dict:
    script = r"""
const vm = require('node:vm');
const slice = SLICE;
const scenario = SCENARIO;

function makeEl(id, tag) {
  const el = {
    id,
    tagName: tag,
    style: {display: tag === 'INPUT' ? 'none' : ''},
    disabled: false,
    classList: {toggle() {}, remove() {}, add() {}},
    textContent: '',
    value: '',
    placeholder: '',
    autocomplete: '',
    spellcheck: true,
    type: 'text',
    _attrs: {},
    _listeners: {},
    isConnected: true,
    contains(node) { return node === el || (node && node._parent === el); },
    setAttribute(name, value) { this._attrs[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this._attrs, name) ? this._attrs[name] : null; },
    addEventListener(type, fn) { (this._listeners[type] || (this._listeners[type] = [])).push(fn); },
    focus() {
      if (!this.isConnected) return;
      document.activeElement = this;
    },
    blur() {
      if (document.activeElement === el) document.activeElement = document.body;
    },
    click() { (this._listeners.click || []).forEach(fn => fn({target: this})); },
    setSelectionRange() {},
    select() {},
  };
  return el;
}

const overlay = makeEl('appDialogOverlay', 'DIV');
overlay._attrs['aria-hidden'] = 'true';
overlay.style.display = 'none';
const dialog = makeEl('appDialog', 'DIV');
const input = makeEl('appDialogInput', 'INPUT');
const cancelBtn = makeEl('appDialogCancel', 'BUTTON');
const confirmBtn = makeEl('appDialogConfirm', 'BUTTON');
const closeBtn = makeEl('appDialogClose', 'BUTTON');
const title = makeEl('appDialogTitle', 'DIV');
const desc = makeEl('appDialogDesc', 'DIV');
const opener = makeEl('opener', 'BUTTON');
for (const child of [dialog, input, cancelBtn, confirmBtn, closeBtn, title, desc]) child._parent = overlay;
dialog._parent = overlay;

const els = {appDialogOverlay: overlay, appDialog: dialog, appDialogInput: input, appDialogCancel: cancelBtn, appDialogConfirm: confirmBtn, appDialogClose: closeBtn, appDialogTitle: title, appDialogDesc: desc};
const body = makeEl('body', 'BODY');
const document = {
  body,
  activeElement: opener,
  getElementById(id) { return els[id] || null; },
  addEventListener() {},
};
const timers = [];
const context = vm.createContext({
  document,
  window: {},
  setTimeout(fn) { timers.push(fn); return timers.length; },
  t: key => key,
});
context.window = context;
vm.runInContext('const $=id=>document.getElementById(id);\n' + slice, context, {timeout: 1000});

function flush() { while (timers.length) timers.shift()(); }
function snapshot(phase) {
  const active = document.activeElement;
  return {
    phase,
    active: active ? active.id : null,
    activeConnected: !!(active && active.isConnected),
    activeInsideHidden: !!(active && overlay.contains(active) && overlay.getAttribute('aria-hidden') === 'true' && overlay.style.display === 'none'),
    ariaHidden: overlay.getAttribute('aria-hidden'),
    display: overlay.style.display,
  };
}

const phases = [];
if (scenario === 'confirm') {
  vm.runInContext('showConfirmDialog({title:"Delete?", message:"sure"})', context);
  flush();
  phases.push(snapshot('open'));
  confirmBtn.click();
  phases.push(snapshot('closed-sync'));
  flush();
  phases.push(snapshot('closed-settled'));
} else if (scenario === 'detached') {
  vm.runInContext('showConfirmDialog({title:"Delete?"})', context);
  flush();
  opener.isConnected = false;
  confirmBtn.click();
  phases.push(snapshot('closed-sync'));
  flush();
  phases.push(snapshot('closed-settled'));
} else {
  throw new Error('unknown scenario ' + scenario);
}
process.stdout.write(JSON.stringify({phases}));
""".replace("SLICE", json.dumps(_dialog_slice())).replace("SCENARIO", json.dumps(scenario))
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_closing_confirm_does_not_leave_focus_inside_the_hidden_dialog():
    phases = {row["phase"]: row for row in _run("confirm")["phases"]}
    assert phases["open"]["active"] == "appDialogConfirm"
    assert phases["open"]["ariaHidden"] == "false"
    closed = phases["closed-sync"]
    assert closed["ariaHidden"] == "true"
    assert closed["display"] == "none"
    assert closed["activeInsideHidden"] is False, (
        "confirm stayed focused inside an aria-hidden, display:none dialog"
    )
    settled = phases["closed-settled"]
    assert settled["active"] == "opener"
    assert settled["activeInsideHidden"] is False


def test_detached_opener_does_not_receive_restored_focus():
    phases = {row["phase"]: row for row in _run("detached")["phases"]}
    closed = phases["closed-sync"]
    assert closed["activeInsideHidden"] is False
    settled = phases["closed-settled"]
    assert settled["activeConnected"] is True
    assert settled["active"] != "opener"
    assert settled["activeInsideHidden"] is False
