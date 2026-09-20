// Run against an isolated, agent-free WebUI with:
// agent-browser eval --stdin < tests/browser_recovery_check.js
// Optional: window.recoveryTerminalEvent = 'done' or 'cancel' (default stream_end).
// HTTP/SSE boundaries are synthetic; all recovery, settlement and DOM code is real.
(async () => {
  const check = (value, message) => { if (!value) throw Error(message); };
  const waitFor = async predicate => {
    const deadline = Date.now() + 6000;
    while (!predicate()) {
      if (Date.now() > deadline) throw Error('Recovery check timed out');
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  const sid = 'recovery-check-' + Date.now(), stream = sid + '-run';
  const terminal = window.recoveryTerminalEvent || 'stream_end';
  const answer = 'Recovered final assistant answer without reloading.';
  const prompt = {role:'user',content:'Show the final recovered answer.'};
  const originalApi = api, originalEventSource = window.EventSource;
  const sources = [], requests = [];
  const session = {session_id:sid,title:'Transparent replay recovery',message_count:5003,
    messages:[prompt,{role:'assistant',content:answer}],tool_calls:[],
    _messages_truncated:true,_messages_offset:5001};
  try {
    window._chatActivityDisplayMode = 'transparent_stream';
    S.session = {...session,messages:[prompt],active_stream_id:stream};
    S.messages = [prompt]; S.toolCalls = []; S.activeStreamId = stream;
    _messagesTruncated = false; _oldestIdx = 0;
    setBusy(true);
    INFLIGHT[sid] = {messages:[prompt],lastRunJournalSeq:206,toolCalls:[]};
    window.EventSource = class extends EventTarget {
      static OPEN = 1; static CONNECTING = 0; static CLOSED = 2;
      constructor(url) { super(); this.url=url; this.readyState=1; sources.push(this); }
      close() { this.readyState=2; }
      emit(type, data, seq) {
        this.dispatchEvent(new MessageEvent(type,{data:JSON.stringify(data),lastEventId:stream+':'+seq}));
      }
    };
    api = async url => {
      requests.push(url);
      if (url.includes('/api/session?')) return {session};
      if (url.includes('/stream/status')) return {active:true,stream_id:stream};
      return {};
    };
    renderMessages();
    attachLiveStream(sid,stream,[],{reconnecting:true});
    await waitFor(() => sources.length === 1);
    check(new URL(sources[0].url).searchParams.get('after_seq') === '0',
      'User-only cache skipped journal prose');
    check(INFLIGHT[sid].messages.some(message => message.role === 'user' && message.content === prompt.content),
      'Cursor reset discarded the pending user prompt');
    sources[0].emit('token',{text:answer},206);
    await waitFor(() => $('messages').innerText.includes(answer));
    sources[0].close();
    attachLiveStream(sid,stream,[],{reconnecting:true});
    await waitFor(() => sources.length === 2);
    check(new URL(sources[1].url).searchParams.get('after_seq') === '206',
      'Recovered assistant text lost its replay cursor');
    sources[1].emit(terminal,terminal === 'done' ? {session} : {},207);
    await waitFor(() => !S.busy && S.activeStreamId === null);
    check($('messages').innerText.includes(answer), 'Settled assistant body is blank');
    check(S.messages.filter(m => m.role === 'assistant').length === 1, 'Duplicated reply');
    check(_messagesTruncated && _oldestIdx === 5001, 'Lost server pagination');
    const sessionRequests = requests.filter(url => url.includes('/api/session?'));
    check(sessionRequests.every(url => new URL(url,location.href).searchParams.get('msg_limit') === '30'),
      'Unbounded recovery session fetch');
    return {terminal,answer,sessionRequests,replayFloors:sources.map(source =>
      new URL(source.url).searchParams.get('after_seq')),truncated:_messagesTruncated,offset:_oldestIdx};
  } finally {
    for (const source of sources) source.close();
    api = originalApi; window.EventSource = originalEventSource;
  }
})()
