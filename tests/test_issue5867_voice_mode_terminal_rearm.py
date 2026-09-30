"""#5867: hands-free voice mode must recover from every stream terminal and
honor the configured silence grace on recognition end.

Two defects fixed in static/boot.js + static/messages.js:

1. Stuck on 'thinking': the only voice-mode exit used to be the done
   handler's autoReadLastAssistant call, so apperror/cancel/network-error
   terminals pinned the indicator at 'thinking' forever. Every terminal
   path already funnels through _setActivePaneIdleIfOwner, which now
   invokes window._voiceModeOnResponseComplete; a watchdog covers turns
   that die before a stream ever opens (e.g. send() fails pre-SSE).
2. Fast onend: Chromium endpointing fires onend well before the
   configured silence grace; the armed _silenceTimer must keep sole
   ownership of the auto-send instead of onend clearing it and sending
   immediately.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.js_source_extract import extract_function


ROOT = Path(__file__).resolve().parents[1]
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _extract_block(src: str, marker: str) -> str:
    """Extract a {...} block starting at the first brace after `marker`."""
    start = src.find(marker)
    assert start >= 0, f"{marker!r} not found"
    brace = src.find("{", start)
    assert brace >= 0, f"{marker!r}: opening brace not found"
    depth = 1
    i = brace + 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{marker!r}: unbalanced braces"
    return src[brace:i]


def _extract_window_assign(src: str, name: str) -> str:
    marker = f"window.{name}=function()"
    start = src.find(marker)
    assert start >= 0, f"window.{name} assignment not found"
    return _extract_block_from(src, start)


def _extract_block_from(src: str, start: int) -> str:
    brace = src.find("{", start)
    assert brace >= 0
    depth = 1
    i = brace + 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0
    return src[start:i] + ";"


# --------------------------------------------------------------------------
# Source-level wiring assertions (no node required)
# --------------------------------------------------------------------------


def test_idle_funnel_invokes_voice_mode_completion_hook():
    idle_body = extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
    assert "setBusy(false)" in idle_body
    assert "window._voiceModeOnResponseComplete" in idle_body, (
        "the shared idle transition must release voice mode's 'thinking' pin"
    )


def test_every_terminal_path_reaches_the_idle_funnel():
    # done keeps the happy-path autoRead hook AND flows through the funnel.
    done_body = _extract_block(MESSAGES_JS, "source.addEventListener('done'")
    assert "autoReadLastAssistant" in done_body
    assert "_setActivePaneIdleIfOwner();" in done_body

    for name in ("apperror", "cancel"):
        body = _extract_block(MESSAGES_JS, f"source.addEventListener('{name}'")
        assert "_setActivePaneIdleIfOwner();" in body, (
            f"{name} terminal must reach _setActivePaneIdleIfOwner"
        )

    # Network 'error' terminals resolve through _handleStreamError.
    err_body = extract_function(MESSAGES_JS, "_handleStreamError")
    assert "_setActivePaneIdleIfOwner();" in err_body


def test_onend_keeps_silence_grace_ownership():
    onend_body = _extract_block(BOOT_JS, "_recognition.onend=")
    assert "clearTimeout(_silenceTimer)" not in onend_body, (
        "onend must not clear the armed silence timer — doing so bypasses "
        "the configured grace and sends on the first endpointed pause"
    )
    assert "if(!_silenceTimer)" in onend_body, (
        "onend should only arm a grace timer when none is pending"
    )

    onresult_body = _extract_block(BOOT_JS, "_recognition.onresult=")
    assert "_voiceModeSend();" in onresult_body
    assert "_voiceSilenceMs()" in onresult_body


def test_voice_mode_send_and_deactivate_clear_pending_timers():
    send_body = extract_function(BOOT_JS, "_voiceModeSend")
    assert "clearTimeout(_silenceTimer);" in send_body

    deactivate_body = extract_function(BOOT_JS, "_deactivate")
    assert "clearTimeout(_silenceTimer);" in deactivate_body
    assert "_clearThinkingWatchdog();" in deactivate_body


def test_thinking_watchdog_declared_and_armed():
    assert "let _thinkingWatchdog=null;" in BOOT_JS
    arm_body = extract_function(BOOT_JS, "_armThinkingWatchdog")
    assert "_thinkingWatchdog=setInterval" in arm_body
    assert "S.busy||S.activeStreamId" in arm_body
    assert "_startListening();" in arm_body

    set_state_body = extract_function(BOOT_JS, "_setState")
    assert "_armThinkingWatchdog();" in set_state_body
    assert "_clearThinkingWatchdog();" in set_state_body


def test_response_complete_hook_defined():
    assert "window._voiceModeOnResponseComplete=function()" in BOOT_JS
    hook = _extract_window_assign(BOOT_JS, "_voiceModeOnResponseComplete")
    assert "_voiceModeState==='thinking'" in hook
    assert "_speakResponse();" in hook


# --------------------------------------------------------------------------
# Behavioral simulation via node (executes the real extracted functions)
# --------------------------------------------------------------------------

pytestmark_node = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(script: str) -> dict:
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"node subprocess failed:\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


_HARNESS = r"""
const calls = [];
globalThis.window = globalThis;
globalThis.localStorage = {
  getItem: (k) => (k === 'hermes-voice-silence-ms' ? '300' : null),
};
const ta = { value: '' };
const indicator = { className: '' };
const label = { textContent: '' };
const bar = { style: {} };
const _locale = { _speech: 'en-US' };
let _voiceModeActive = true;
let _voiceModeState = 'idle';
let _recognition = null;
let _silenceTimer = null;
let _voiceModeThinkingSid = null;
let _browserTtsKeepAlive = null;
let _browserTtsWatchdog = null;
let _browserTtsSuppressNextErrorRearm = false;
let _thinkingWatchdog = null;
const S = { session: { session_id: 'sid-5867' }, busy: false, activeStreamId: null };
let sendCalls = 0;
const SEND_LIVE = __SEND_LIVE__;
function send() {
  sendCalls += 1;
  if (SEND_LIVE) { S.busy = true; S.activeStreamId = 'st-1'; }
}
function _speakResponse() { calls.push('speak'); _setState('speaking'); }
function t(k) { return k; }
function showToast() {}
function autoResize() {}
function _micOriginNeedsSecureContext() { return false; }
function _deactivate() {}
let _recInstance = null;
class SpeechRecognition {
  constructor() { _recInstance = this; }
  start() {}
  abort() { calls.push('abort'); if (this.onend) this.onend(); }
  stop() {}
}
// Compress the thinking watchdog's real interval so the test stays fast;
// the polling logic itself is unchanged.
const _origSetInterval = globalThis.setInterval;
globalThis.setInterval = (fn, ms, ...a) => _origSetInterval(fn, ms >= 4000 ? 25 : ms, ...a);
__FNS__
// Record invocations of the extracted _startListening for assertions.
const _innerStartListening = _startListening;
_startListening = function () { calls.push('listen'); return _innerStartListening.apply(this, arguments); };
"""


def _harness(extra_fns: str = "", send_live: bool = True) -> str:
    fns = "\n".join(
        [
            extract_function(BOOT_JS, "_voiceSilenceMs"),
            extract_function(BOOT_JS, "_clearBrowserTtsRecovery"),
            extract_function(BOOT_JS, "_clearThinkingWatchdog"),
            extract_function(BOOT_JS, "_armThinkingWatchdog"),
            extract_function(BOOT_JS, "_setState"),
            extract_function(BOOT_JS, "_startListening"),
            extract_function(BOOT_JS, "_voiceModeSend"),
            _extract_window_assign(BOOT_JS, "_voiceModeOnResponseComplete"),
            extra_fns,
        ]
    )
    return _HARNESS.replace("__FNS__", fns).replace(
        "__SEND_LIVE__", "true" if send_live else "false"
    )


@pytestmark_node
def test_onend_defers_to_silence_grace_timer():
    """Fast onend after a final result must NOT send immediately — the armed
    silence timer stays the sole auto-send gate (#5867 problem 2)."""
    script = (
        _harness()
        + r"""
    _startListening();
    const rec = _recInstance;
    rec.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'hello there' }, isFinal: true }],
    });
    rec.onend(); // endpointing fires right after the final result
    setTimeout(() => {
      calls.push(['early', sendCalls, _voiceModeState]);
    }, 80);
    setTimeout(() => {
      calls.push(['late', sendCalls, _voiceModeState]);
      console.log(JSON.stringify(calls));
      process.exit(0);
    }, 600);
    """
    )
    calls = _run_node(script)
    early = next(c for c in calls if c[0] == "early")
    late = next(c for c in calls if c[0] == "late")
    assert early[1] == 0 and early[2] == "listening", (
        f"fast onend must not send before the silence grace: {calls}"
    )
    assert late[1] == 1 and late[2] == "thinking", (
        f"silence timer should own the send after the grace: {calls}"
    )


@pytestmark_node
def test_terminal_hook_recovers_thinking_to_speaking():
    """A stream terminal (apperror/cancel/error) reaches
    window._voiceModeOnResponseComplete via the idle funnel, releasing the
    'thinking' pin into the speak transition (#5867 problem 1)."""
    script = (
        _harness()
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'what time is it' }, isFinal: true }],
    });
    setTimeout(() => {
      // Simulate the terminal-path funnel call (e.g. apperror tail).
      window._voiceModeOnResponseComplete();
    }, 450);
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState, sendCalls }));
      process.exit(0);
    }, 1000);
    """
    )
    out = _run_node(script)
    assert out["sendCalls"] == 1
    assert "speak" in out["calls"], (
        f"terminal hook must release thinking into speak: {out}"
    )
    assert out["state"] == "speaking"


@pytestmark_node
def test_thinking_watchdog_recovers_when_no_terminal_arrives():
    """If send() fails before any stream opens (no terminal event ever
    fires), the watchdog must re-arm listening instead of pinning at
    'thinking' forever."""
    script = (
        _harness(send_live=False)
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'hello' }, isFinal: true }],
    });
    // grace fires ~300ms -> _voiceModeSend -> send() records but no stream
    // ever becomes live -> watchdog (compressed to 25ms) re-arms.
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState, sendCalls }));
      process.exit(0);
    }, 800);
    """
    )
    out = _run_node(script)
    assert out["sendCalls"] == 1
    assert "listen" in out["calls"], (
        f"watchdog must re-arm listening after sustained dead 'thinking': {out}"
    )
    assert out["state"] == "listening"


@pytestmark_node
def test_onend_without_text_restarts_listening():
    """No captured text keeps the pre-existing restart behavior."""
    script = (
        _harness()
        + r"""
    _startListening();
    _recInstance.onend(); // ended with no speech
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState, sendCalls }));
      process.exit(0);
    }, 700);
    """
    )
    out = _run_node(script)
    assert out["sendCalls"] == 0
    assert "listen" in out["calls"]
    assert out["state"] == "listening"
