"""Behavioral client regressions: replay coverage and bounded tail refreshes."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(os.environ.get("RECOVERY_TEST_STATIC", ROOT / "static"))
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node required")


def function(file, name):
    source = (STATIC / file).read_text()
    start = source.index("function " + name + "(")
    if source[max(0, start - 6):start] == "async ":
        start -= 6
    brace = source.index("{", source.index(")", start))
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def run(script):
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_user_prompt_does_not_authorize_skipping_journal_output():
    run(function("sessions.js", "_inflightHasVisibleLiveState") + r"""
const assert = require('node:assert/strict');
const _messageComparableText = m => m.content;
const prompt = {role:'user', content:'Please answer', _live:true};
const stale = {messages:[prompt], lastRunJournalSeq:206};
assert.equal(_inflightHasVisibleLiveState(stale), false);
assert.equal(_inflightHasVisibleLiveState({...stale, messages:[
  {role:'assistant',content:'An earlier turn'}, prompt]}), false);
for (const state of [
  {...stale,lastAssistantText:'Answer'},
  {...stale,lastReasoningText:'Thinking'},
  {...stale,toolCalls:[{name:'terminal'}]},
  {...stale,messages:[prompt,{role:'assistant',_live:true,reasoning:'Thinking'}]},
  {...stale,anchorActivityScene:{activity_rows:[{text:'Answer',role:'prose'}]}}
]) assert.equal(_inflightHasVisibleLiveState(state), true);
""")


@pytest.mark.parametrize("file,name", [
    ("ui.js", "refreshSession"),
    ("commands.js", "cmdUndo"),
    ("commands.js", "cmdRetry"),
])
def test_refresh_requests_tail_and_keeps_pagination_and_tools(file, name):
    run(function(file, name) + r"""
const assert = require('node:assert/strict');
const window = {};
const S = {session:{session_id:'s'},messages:[],toolCalls:[]};
let _messagesTruncated = false, _oldestIdx = 0, rendered = 0;
const noop = () => {};
const dismissReconnect=noop, syncTopbar=noop, showToast=noop, clearLiveToolCards=noop;
const renderMessages=()=>rendered++, _renderMessagesWithScrollSnapshot=renderMessages;
const setStatus = text => {throw Error(text)};
const t = x => x, getPendingSessionMessage = () => null, $ = () => ({}), send=noop;
const session = {session_id:'s',messages:[{role:'assistant',content:'Final answer'}],
  tool_calls:[{name:'terminal'}],_messages_truncated:true,_messages_offset:4973};
async function api(url, options) {
  if(options?.method === 'POST') return {removed_count:2,last_user_text:'Retry'};
  const query = new URL(url,'http://example.test').searchParams;
  assert.equal(query.get('msg_limit'),'30');
  assert.equal(query.get('resolve_model'),'0');
  return {session};
}
""" + f"""
(async()=>{{
  await {name}();
  assert.equal(rendered,1);
  assert.equal(_messagesTruncated,true);
  assert.equal(_oldestIdx,4973);
  assert.deepEqual(S.toolCalls,session.tool_calls);
  assert.equal(S.messages[0].content,'Final answer');
}})().catch(e=>{{console.error(e);process.exit(1)}});
""")


def test_compression_preflight_does_not_fetch_or_erase_history():
    run(function("commands.js", "_runManualCompression") + r"""
const assert = require('node:assert/strict');
const messages = [{role:'assistant',content:'Existing reply'}], tools=[{name:'terminal'}];
const S = {session:{session_id:'s'},messages,toolCalls:tools};
let _messagesTruncated=true, _oldestIdx=4973;
const t=x=>x, setBusy=()=>{}, renderMessages=()=>{};
const showToast=text=>{throw Error(text)};
const _manualCompressionVisibleMessages=()=>S.messages;
const _compressionAnchorMessageKey=()=>'';
const _applyManualCompressionResult=async()=>{};
async function api(url) {
  if(url.includes('?')) {
    assert.equal(new URL(url,'http://test').searchParams.get('messages'),'0');
    return {session:{session_id:'s',messages:[],tool_calls:[]}};
  }
  assert.equal(S.messages,messages);
  assert.equal(S.toolCalls,tools);
  assert.equal(_messagesTruncated,true);
  assert.equal(_oldestIdx,4973);
  return {status:'done'};
}
_runManualCompression('').catch(e=>{console.error(e);process.exit(1)});
""")


def test_terminal_restore_fetch_is_bounded():
    # Execute through the HTTP boundary; an active response deliberately avoids
    # finalization so this test is independent of renderer internals.
    run(function("messages.js", "_restoreSettledSession") + r"""
const assert = require('node:assert/strict');
const activeSid='s', streamId='run', S={activeStreamId:'run'};
let _streamFinalized=false;
const _isActiveSession=()=>true;
async function api(url) {
  const query=new URL(url,'http://test').searchParams;
  if(query.get('msg_limit')!=='30') return null;
  return {session:{active_stream_id:'run'}};
}
_restoreSettledSession({}, {status:true}).then(result=>{
  assert.equal(result,'active');
}).catch(e=>{console.error(e);process.exit(1)});
""")


def test_outline_full_history_jump_keeps_absolute_target_and_pagination():
    run(function("outline.js", "_jumpToMessage") + r"""
const assert=require('node:assert/strict');
let _oldestIdx=4973, _messagesTruncated=true, jumped='';
const S={session:{session_id:'s'},messages:[]};
const _currentSid=()=> 's', _flashRow=()=>{}, _expandOutlineRenderWindow=()=>{};
let rendered=false;
const renderMessages=()=>{rendered=true};
const document={getElementById:id=>rendered?{scrollIntoView:()=>{jumped=id}}:null};
const window={setTimeout:callback=>callback()};
async function api(url) {
  assert.equal(new URL(url,'http://test').searchParams.has('msg_limit'),false);
  return {session:{messages:[{role:'user',content:'All history'}]}};
}
_jumpToMessage(4);
setImmediate(()=>{
  assert.equal(jumped,'msg-user-4977');
  assert.equal(_messagesTruncated,false);
  assert.equal(_oldestIdx,0);
});
""")


def test_compression_result_resets_tail_metadata_for_full_transcript():
    run(function("commands.js", "_applyManualCompressionResult") + r"""
const assert=require('node:assert/strict');
const S={session:{session_id:'s'},messages:[],toolCalls:[]};
let _messagesTruncated=true, _oldestIdx=4973;
const clearLiveToolCards=()=>{}, syncTopbar=()=>{}, renderMessages=()=>{};
const renderSessionList=async()=>{}, updateQueueBadge=()=>{};
_applyManualCompressionResult({session:{session_id:'s',messages:[]}},'',30,'/compress').then(()=>{
  assert.equal(_messagesTruncated,false);
  assert.equal(_oldestIdx,0);
}).catch(e=>{console.error(e);process.exit(1)});
""")
