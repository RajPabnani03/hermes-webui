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

Follow-up review hardening (terminal outcome handling):
- The funnel now passes an outcome into the hook: only 'done' speaks the
  last assistant row; cancel/error/settled terminals resume listening
  silently instead of reading the partial reply or cancel marker aloud.
- The deferred done->speak callback captures a turn token; a new 'thinking'
  claim invalidates any stale timer so a previous turn can't speak over a
  new stream.
- The thinking watchdog holds when send() restored a non-empty draft into
  the composer — resuming recognition would overwrite it.

Second review hardening (stream ownership):
- The funnel also reports which session's stream settled ({sessionId:
  activeSid, streamId}); the hook returns unless that id matches
  _voiceModeThinkingSid, so a background stream's terminal can't release
  the voice-mode owner of a different session.
- Non-done outcomes also hold 'thinking' while the composer holds a
  restored draft — the funnel path bypasses the watchdog's draft guard.
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
    marker = f"window.{name}=function"
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
    # The hook must learn which session's stream settled — a background
    # terminal must not release the owner pinned to another session.
    assert "sessionId:activeSid" in idle_body
    assert "streamId:streamId" in idle_body


def test_every_terminal_path_reaches_the_idle_funnel():
    # done keeps the happy-path autoRead hook AND flows through the funnel
    # with an explicit 'done' outcome (the only speakable terminal).
    done_body = _extract_block(MESSAGES_JS, "source.addEventListener('done'")
    assert "autoReadLastAssistant" in done_body
    assert "_setActivePaneIdleIfOwner('done');" in done_body

    apperror_body = _extract_block(MESSAGES_JS, "source.addEventListener('apperror'")
    assert "_setActivePaneIdleIfOwner('error');" in apperror_body
    cancel_body = _extract_block(MESSAGES_JS, "source.addEventListener('cancel'")
    assert "_setActivePaneIdleIfOwner('cancel');" in cancel_body

    # Network 'error' terminals resolve through _handleStreamError; the
    # stream_end fallback path is likewise not a clean completion.
    err_body = extract_function(MESSAGES_JS, "_handleStreamError")
    assert "_setActivePaneIdleIfOwner('error');" in err_body
    fallback_body = extract_function(MESSAGES_JS, "_finalizeStreamEndFallback")
    assert "_setActivePaneIdleIfOwner('error');" in fallback_body

    # A settled-session restore observed no terminal event at all — the last
    # row may be a cancel marker, so it resumes listening without speech.
    restore_body = extract_function(MESSAGES_JS, "_restoreSettledSession")
    assert "_setActivePaneIdleIfOwner('settled');" in restore_body


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
    # A restored unsent draft must survive the re-arm: recognition results
    # write straight into the textarea.
    assert "ta.value" in arm_body, (
        "watchdog must not resume recognition over a non-empty restored draft"
    )

    set_state_body = extract_function(BOOT_JS, "_setState")
    assert "_armThinkingWatchdog();" in set_state_body
    assert "_clearThinkingWatchdog();" in set_state_body
    # A fresh 'thinking' claim invalidates a previous turn's deferred speak
    # callback and clears its pending timer.
    assert "_voiceModeTurnSeq" in set_state_body
    assert "_voiceModeResponseTimer" in set_state_body


def test_response_complete_hook_defined():
    assert "window._voiceModeOnResponseComplete=function" in BOOT_JS
    hook = _extract_window_assign(BOOT_JS, "_voiceModeOnResponseComplete")
    assert "_voiceModeState==='thinking'" in hook
    assert "_speakResponse();" in hook
    # Terminal outcome gates speech: only an explicit 'done' may read the
    # last assistant row aloud; cancel/error resume listening silently.
    assert "details.outcome" in hook
    # Stream-ownership gate: a terminal whose sessionId differs from the
    # pinned voice-mode owner is ignored entirely.
    assert "details.sessionId" in hook
    assert "_voiceModeThinkingSid" in hook
    # The deferred speak re-checks that the scheduling turn still owns the
    # 'thinking' state before reading anything aloud.
    assert "_voiceModeTurnSeq" in hook
    assert "_voiceModeResponseTimer" in hook


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
let _voiceModeResponseTimer = null;
let _voiceModeTurnSeq = 0;
const S = { session: { session_id: 'sid-5867' }, busy: false, activeStreamId: null };
let sendCalls = 0;
const SEND_LIVE = __SEND_LIVE__;
function send() {
  sendCalls += 1;
  const text = ta.value;
  ta.value = ''; // send() consumes the composer contents up front
  if (SEND_LIVE) { S.busy = true; S.activeStreamId = 'st-1'; }
  else { ta.value = text; } // a pre-stream failure restores the unsent draft
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
    """If the stream dies without a terminal event reaching the funnel (and
    the composer holds no restored draft), the watchdog must re-arm
    listening instead of pinning at 'thinking' forever."""
    script = (
        _harness(send_live=True)
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'hello' }, isFinal: true }],
    });
    // grace fires ~300ms -> _voiceModeSend -> send() consumes the composer
    // and opens the stream, which then dies without ever emitting a
    // terminal event -> watchdog (compressed to 25ms) re-arms listening.
    setTimeout(() => { S.busy = false; S.activeStreamId = null; }, 350);
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


_FUNNEL_SETUP = r"""
// _setActivePaneIdleIfOwner's closure deps — route the cancel terminal
// through the real funnel, not a direct hook call. activeSid/streamId are
// attachLiveStream's closure params in the real code: they name the stream
// that settled (the terminal's source), not the UI's active session.
let activeSid = 'sid-5867';
let streamId = 'st-1';
function _isActiveSession() { return true; }
const INFLIGHT = { 'sid-5867': { messages: [] } };
let setBusyCalls = [];
function setBusy(v) { setBusyCalls.push(v); S.busy = v; }
function setComposerStatus() {}
function setStatus() {}
"""

_FUNNEL_SETUP_BACKGROUND = r"""
// A background stream's terminal: the UI session ('sid-5867') is not the
// stream's owner, and the owner session has no INFLIGHT entry — the broad
// idle condition still admits the hook call, so ownership must gate inside it.
let activeSid = 'bg-sid';
let streamId = 'st-bg';
function _isActiveSession() { return false; }
const INFLIGHT = {};
let setBusyCalls = [];
function setBusy(v) { setBusyCalls.push(v); S.busy = v; }
function setComposerStatus() {}
function setStatus() {}
"""


@pytestmark_node
def test_cancel_terminal_funnel_resumes_listening_without_speech():
    """Stop/cancel funnels _setActivePaneIdleIfOwner('cancel') into the hook;
    voice mode must clear 'thinking' and go straight back to listening
    without reading the partial reply or the cancel marker aloud."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'draft' }, isFinal: true }],
    });
    // silence grace (~300ms) -> _voiceModeSend -> 'thinking', stream live
    setTimeout(() => { _setActivePaneIdleIfOwner('cancel'); }, 450);
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState }));
      process.exit(0);
    }, 1000);
    """
    )
    out = _run_node(script)
    assert "speak" not in out["calls"], (
        f"cancel terminal must not read the partial reply aloud: {out}"
    )
    assert "listen" in out["calls"], (
        f"cancel terminal must resume listening: {out}"
    )
    assert out["state"] == "listening"


@pytestmark_node
def test_error_terminal_funnel_resumes_listening_without_speech():
    """apperror/network-error terminals take the same silent re-arm path."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'draft' }, isFinal: true }],
    });
    setTimeout(() => { _setActivePaneIdleIfOwner('error'); }, 450);
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState }));
      process.exit(0);
    }, 1000);
    """
    )
    out = _run_node(script)
    assert "speak" not in out["calls"]
    assert "listen" in out["calls"]
    assert out["state"] == "listening"


@pytestmark_node
def test_done_terminal_still_speaks_through_funnel():
    """The 'done' outcome keeps the delayed speak path — regression guard for
    the outcome gating itself (voice mode off is covered by _voiceModeActive
    checks inside the real hook)."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'draft' }, isFinal: true }],
    });
    setTimeout(() => { _setActivePaneIdleIfOwner('done'); }, 450);
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState }));
      process.exit(0);
    }, 1400);
    """
    )
    out = _run_node(script)
    assert "speak" in out["calls"], f"done terminal must still speak: {out}"
    assert out["state"] == "speaking"


@pytestmark_node
def test_stale_done_callback_cannot_speak_over_new_turn():
    """A done terminal's delayed speak must not fire once a new turn has
    claimed 'thinking' — the turn token invalidates the stale callback even
    though state is 'thinking' again when it fires."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'first' }, isFinal: true }],
    });
    // Turn 1 sends (~300ms) and its done terminal schedules speak at +400ms.
    setTimeout(() => { _setActivePaneIdleIfOwner('done'); }, 450);
    // 100ms later the user starts a new turn: state returns to 'thinking'
    // under a fresh turn token before the stale callback's 400ms deadline.
    setTimeout(() => {
      _setState('listening');   // user-visible transition between turns
      ta.value = 'next turn';   // the new draft the user is sending
      _voiceModeSend();
    }, 550);
    setTimeout(() => {
      console.log(JSON.stringify({ calls, state: _voiceModeState, sendCalls }));
      process.exit(0);
    }, 1400);
    """
    )
    out = _run_node(script)
    assert "speak" not in out["calls"], (
        f"stale turn-1 callback must not speak over the new stream: {out}"
    )
    assert out["sendCalls"] == 2
    assert out["state"] == "thinking"


@pytestmark_node
def test_background_terminal_cannot_release_voice_owner():
    """Session B owns voice mode ('thinking', failed-send draft restored in
    the composer) while background stream A settles. The broad idle
    condition admits the hook call (_isActiveSession false, no INFLIGHT[B]),
    but A's terminal must not release B: state, owner token, and draft are
    untouched and recognition does not resume."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP_BACKGROUND
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'draft' }, isFinal: true }],
    });
    // grace (~300ms) -> _voiceModeSend pins _voiceModeThinkingSid='sid-5867'
    // and enters 'thinking'.
    setTimeout(() => {
      ta.value = 'restored draft';  // send() failure restored the draft
      S.busy = false;               // B's send is between admission stages
      _setActivePaneIdleIfOwner('error'); // stream A's terminal reaches the funnel
    }, 450);
    setTimeout(() => {
      console.log(JSON.stringify({
        calls, state: _voiceModeState,
        owner: _voiceModeThinkingSid, draft: ta.value,
      }));
      process.exit(0);
    }, 1000);
    """
    )
    out = _run_node(script)
    # The only 'listen' is the initial _startListening() — nothing after the
    # background terminal may resume recognition or speak for session B.
    assert out["calls"].count("listen") == 1 and "speak" not in out["calls"], (
        f"background terminal must not touch the other session's voice mode: {out}"
    )
    assert out["state"] == "thinking"
    assert out["owner"] == "sid-5867"
    assert out["draft"] == "restored draft"


@pytestmark_node
def test_error_terminal_preserves_restored_draft():
    """Same-session complement: B's own error terminal with a restored draft
    in the composer holds 'thinking' instead of resuming recognition over
    the draft. Clearing the draft lets the watchdog resume listening."""
    script = (
        _harness(
            extra_fns=extract_function(MESSAGES_JS, "_setActivePaneIdleIfOwner")
        )
        + _FUNNEL_SETUP
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'draft' }, isFinal: true }],
    });
    setTimeout(() => {
      ta.value = 'restored draft';
      S.busy = false; S.activeStreamId = null;
      _setActivePaneIdleIfOwner('error'); // B's own terminal
    }, 450);
    setTimeout(() => {
      calls.push(['withDraft', _voiceModeState, ta.value]);
      ta.value = '';                    // user clears the restored draft
    }, 750);
    setTimeout(() => {
      calls.push(['afterClear', _voiceModeState]);
      console.log(JSON.stringify(calls));
      process.exit(0);
    }, 1000);
    """
    )
    calls = _run_node(script)
    with_draft = next(c for c in calls if c[0] == "withDraft")
    after_clear = next(c for c in calls if c[0] == "afterClear")
    assert "speak" not in calls
    assert with_draft[1] == "thinking" and with_draft[2] == "restored draft", (
        f"error terminal must not resume recognition over a restored draft: {calls}"
    )
    assert after_clear[1] == "listening", (
        f"clearing the draft must let the watchdog resume listening: {calls}"
    )


@pytestmark_node
def test_thinking_watchdog_preserves_restored_draft():
    """send() failing pre-stream restores the typed text into the composer.
    The watchdog must not resume recognition while that draft sits there —
    the next onresult would overwrite it — but must resume once cleared."""
    script = (
        _harness(send_live=False)
        + r"""
    _startListening();
    _recInstance.onresult({
      resultIndex: 0,
      results: [{ 0: { transcript: 'hello' }, isFinal: true }],
    });
    // grace (~300ms) -> _voiceModeSend -> send() fails pre-stream and
    // restores the draft into the composer.
    // Watchdog polls at 25ms (compressed); after ~450ms it would normally
    // have resumed listening — with the draft it must hold 'thinking'.
    setTimeout(() => {
      calls.push(['withDraft', _voiceModeState]);
      ta.value = '';          // user clears the restored draft
    }, 750);
    setTimeout(() => {
      calls.push(['afterClear', _voiceModeState]);
      console.log(JSON.stringify(calls));
      process.exit(0);
    }, 950);
    """
    )
    calls = _run_node(script)
    with_draft = next(c for c in calls if c[0] == "withDraft")
    after_clear = next(c for c in calls if c[0] == "afterClear")
    assert with_draft[1] == "thinking", (
        f"watchdog must not resume recognition over a restored draft: {calls}"
    )
    assert after_clear[1] == "listening", (
        f"clearing the draft must let the watchdog resume listening: {calls}"
    )
