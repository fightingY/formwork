"""Web chat server for V5 conversation sessions (experimental).

Pure-stdlib HTTP + SSE front-end for :class:`minicc.core.session_engine.SessionEngine`,
mirroring ``server/app.py`` (``ThreadingHTTPServer`` + ``BaseHTTPRequestHandler``) so
the project keeps its zero-third-party-dependency rule.  See
``docs/V5_0_SESSION_CHAT_REMODEL_PLAN.md`` §7 for the API surface.

This module deliberately has no notion of provider/policy wiring: the caller
(``cli.py``) injects ``engine_factory``, a callable that returns a *deferred*
:class:`SessionEngine` (no ``on_approval`` callback).  In web mode a destructive
command therefore pauses the turn as ``waiting_approval`` and the front-end
resolves it through the ``approve|deny`` endpoints, mirroring the REPL's HITL
without a blocking stdin read on the server.

Endpoints:

    GET  /                                    single-page chat UI
    GET  /api/sessions                        list sessions
    POST /api/sessions                        create session {project_root}
    GET  /api/sessions/<id>/transcript        transcript rows
    GET  /api/sessions/<id>/events            SSE: stream turn events for <id>
    POST /api/sessions/<id>/messages          send a user message (runs a turn)
    POST /api/sessions/<id>/steer             steering message (redirects the turn)
    POST /api/sessions/<id>/runs/<rid>/approve   resolve an approval gate
    POST /api/sessions/<id>/runs/<rid>/deny      reject an approval gate

``steer`` is a best-effort redirect, not a hard cancel: the agent loop has no
mid-turn cancellation token yet (plan §5 keeps checkpoint/interrupt for that),
so a steering message is applied by starting a fresh turn with it once the
current one finishes.  It is documented here honestly rather than over-claimed.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from minicc.core.session_engine import SessionEngine, SessionTurnResult
from minicc.core.session_store import SessionNotFoundError, SessionStore

EngineFactory = Callable[[], SessionEngine]


# --- pure helpers (the deterministic test surface) ---------------------------


def sessions_payload(store: SessionStore) -> list[dict[str, Any]]:
    """Session list reduced to JSON-friendly dicts (no dataclass leakage)."""
    return [
        {
            "session_id": record.session_id,
            "project_root": record.project_root,
            "title": record.title,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "turn_count": len(record.turns),
        }
        for record in store.list_sessions()
    ]


def transcript_payload(store: SessionStore, session_id: str) -> list[dict[str, Any]]:
    """Transcript rows in their on-disk shape (seq/run_id/role/content)."""
    return [message.to_dict() for message in store.read_transcript(session_id)]


def turn_result_to_dict(result: SessionTurnResult) -> dict[str, Any]:
    """Project a :class:`SessionTurnResult` into a wire-safe dict.

    ``pending_command``/``approval_question`` carry the gated action so the UI
    can render approve/deny without re-reading the run.
    """
    action = result.state.pending_action
    return {
        "run_id": result.run_id,
        "user_message": result.user_message,
        "assistant_reply": result.assistant_reply,
        "status": result.status,
        "pending_command": action.command if action is not None else None,
        "approval_question": result.state.approval_question,
    }


def _turn_event(result: SessionTurnResult) -> dict[str, Any]:
    event = turn_result_to_dict(result)
    event["type"] = (
        "turn_waiting_approval" if result.status == "waiting_approval" else "turn_done"
    )
    return event


def execute_turn(
    engine_factory: EngineFactory,
    session_id: str,
    message: str,
) -> dict[str, Any]:
    """Run one turn against a *deferred* engine and shape it as an SSE event."""
    return _turn_event(engine_factory().submit_turn(session_id, message))


def resolve_approval(
    engine_factory: EngineFactory,
    session_id: str,
    run_id: str,
    decision: str,
) -> dict[str, Any]:
    """Resume a paused turn with an approve/deny decision."""
    return _turn_event(engine_factory().resolve_turn(session_id, run_id, decision))


def _is_safe_session_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _is_safe_run_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


# --- SSE broker --------------------------------------------------------------

SSE_HEARTBEAT_SECONDS = 15


class ChatBroker:
    """Thread-safe pub/sub between turn workers and SSE subscribers.

    Every ``publish`` fans out to the session channel *and* a global ``"*"``
    channel (used by the sidebar for live session-list refreshes).  Subscribers
    hold a bounded ``queue.Queue``; a slow subscriber is dropped rather than
    letting its backlog grow without bound.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, session_id: str) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.setdefault(session_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, session_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            buckets = self._subscribers.get(session_id)
            if buckets is None:
                return
            if subscriber in buckets:
                buckets.remove(subscriber)
            if not buckets:
                self._subscribers.pop(session_id, None)

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(session_id, [])) + list(
                self._subscribers.get("*", [])
            )
        for subscriber in targets:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # A back-pressured viewer misses the event rather than blocking a turn.
                continue


# --- HTTP handler ------------------------------------------------------------


def serve_chat(
    *,
    store: SessionStore,
    engine_factory: EngineFactory,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the web chat server and block until interrupted."""
    broker = ChatBroker()
    handler = _handler_for(store, engine_factory, broker)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Formwork chat (experimental): http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChat stopped.")
    finally:
        server.server_close()


def _handler_for(
    store: SessionStore,
    engine_factory: EngineFactory,
    broker: ChatBroker,
) -> type[BaseHTTPRequestHandler]:
    class ChatHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            segments = self._segments()
            if segments == []:
                self._send_html(render_chat_index())
                return
            if segments == ["api", "sessions"]:
                self._send_json(sessions_payload(store))
                return
            if self._is(segments, ["api", "sessions"], tail=2) and segments[3] == "transcript":
                self._send_json(transcript_payload(store, segments[2]))
                return
            if self._is(segments, ["api", "sessions"], tail=2) and segments[3] == "events":
                self._handle_events(segments[2])
                return
            self._not_found()

        def do_POST(self) -> None:
            segments = self._segments()
            body = self._read_json_body()
            if segments == ["api", "sessions"]:
                project_root = str(body.get("project_root") or "").strip() or str(Path.cwd())
                record = store.create(project_root)
                broker.publish("*", {"type": "session_created", "session_id": record.session_id})
                self._send_json(
                    {"session_id": record.session_id, "project_root": record.project_root}
                )
                return
            if self._is(segments, ["api", "sessions"], tail=2) and segments[3] == "messages":
                message = str(body.get("message") or body.get("content") or "").strip()
                if not message:
                    self._send_error(400, "message is required")
                    return
                self._run_and_respond(segments[2], lambda: execute_turn(engine_factory, segments[2], message))
                return
            if self._is(segments, ["api", "sessions"], tail=2) and segments[3] == "steer":
                message = str(body.get("message") or "").strip()
                if not message:
                    self._send_error(400, "message is required")
                    return
                broker.publish(segments[2], {"type": "steer", "message": message})
                self._run_and_respond(segments[2], lambda: execute_turn(engine_factory, segments[2], message))
                return
            if (
                    len(segments) == 6
                    and segments[:2] == ["api", "sessions"]
                    and segments[3] == "runs"
                    and segments[5] in {"approve", "deny"}
                ):
                    session_id, run_id, verb = segments[2], segments[4], segments[5]
                    self._resolve_and_respond(session_id, run_id, verb, body)
                    return
            self._not_found()

        # -- helpers ---------------------------------------------------------

        def _segments(self) -> list[str]:
            return [seg for seg in unquote(urlsplit(self.path).path).split("/") if seg]

        @staticmethod
        def _is(segments: list[str], prefix: list[str], *, tail: int) -> bool:
            return len(segments) == len(prefix) + tail and segments[: len(prefix)] == prefix

        def _run_and_respond(self, session_id: str, run: Callable[[], dict[str, Any]]) -> None:
            """Run a turn; publish its event to SSE subscribers and return it."""
            try:
                event = run()
            except SessionNotFoundError:
                self._send_error(404, "Session not found")
                return
            broker.publish(session_id, event)
            self._send_json(event)

        def _resolve_and_respond(
            self,
            session_id: str,
            run_id: str,
            verb: str,
            body: dict[str, Any],
        ) -> None:
            if not (_is_safe_session_id(session_id) and _is_safe_run_id(run_id)):
                self._send_error(404, "Not found")
                return
            reason = str(body.get("reason") or "").strip()
            decision = "approve" if verb == "approve" else (f"deny: {reason}" if reason else "deny")
            try:
                event = resolve_approval(engine_factory, session_id, run_id, decision)
            except SessionNotFoundError:
                self._send_error(404, "Session not found")
                return
            except ValueError as exc:
                self._send_error(409, str(exc))
                return
            broker.publish(session_id, event)
            self._send_json(event)

        def _handle_events(self, session_id: str) -> None:
            if not _is_safe_session_id(session_id):
                self._send_error(404, "Session not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            subscriber = broker.subscribe(session_id)
            try:
                self._sse_write({"type": "snapshot", "transcript": transcript_payload(store, session_id)})
                while True:
                    try:
                        event = subscriber.get(timeout=SSE_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        self._sse_write({"type": "heartbeat"})
                        continue
                    self._sse_write(event)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                broker.unsubscribe(session_id, subscriber)

        def _sse_write(self, event: dict[str, Any]) -> None:
            payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
            self.wfile.write(b"data: " + payload + b"\n\n")
            self.wfile.flush()

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def _send_html(self, content: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        def _send_json(self, payload: Any) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

        def _send_error(self, code: int, message: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            )

        def _not_found(self) -> None:
            self._send_error(404, "Not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ChatHandler


# --- single-page front-end ---------------------------------------------------


def render_chat_index() -> str:
    """One-page chat UI; all data flows through the JSON/SSE API above."""
    return _CHAT_INDEX_HTML


_CHAT_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Formwork Chat</title>
  <style>
    :root {
      --bg: #f6f7f9; --panel: #ffffff; --ink: #18222f; --muted: #66717f;
      --line: #d8dee7; --accent: #0f766e; --accent-soft: #d9f0ed; --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
    button, input { font: inherit; }
    button { border: 1px solid var(--line); background: var(--panel); border-radius: 6px;
      padding: 7px 11px; cursor: pointer; }
    button:hover { border-color: var(--accent); }
    .app { display: grid; grid-template-rows: auto 1fr; min-height: 100vh; }
    header { display: flex; align-items: center; justify-content: space-between;
      padding: 12px 18px; border-bottom: 1px solid var(--line); background: var(--panel); }
    h1 { margin: 0; font-size: 18px; }
    .subtle { color: var(--muted); font-size: 12px; }
    main { display: grid; grid-template-columns: 300px 1fr; min-height: 0; }
    aside { border-right: 1px solid var(--line); background: var(--panel);
      display: grid; grid-template-rows: auto 1fr; min-height: 0; }
    .side-tools { display: grid; gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }
    .session-list { overflow: auto; padding: 8px; }
    .session-item { width: 100%; text-align: left; margin-bottom: 8px; padding: 10px;
      display: grid; gap: 3px; }
    .session-item.active { border-color: var(--accent); background: #f0fbf9; }
    .session-title { font-weight: 700; overflow-wrap: anywhere; }
    .session-sub { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .chat { display: grid; grid-template-rows: auto 1fr auto; min-height: 0; }
    .approval-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      padding: 10px 14px; border-bottom: 1px solid var(--line); background: #fff2d6; }
    .approval-bar .grow { flex: 1; min-width: 200px; }
    .approval-command { font-family: monospace; color: var(--danger); }
    .messages { overflow: auto; padding: 16px; display: grid; gap: 12px; align-content: start; }
    .bubble { max-width: 76%; padding: 10px 13px; border-radius: 10px; white-space: pre-wrap;
      overflow-wrap: anywhere; }
    .bubble.user { justify-self: end; background: var(--accent-soft); }
    .bubble.assistant { justify-self: start; background: #fff; border: 1px solid var(--line); }
    .bubble .who { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
    .composer { display: grid; gap: 8px; padding: 12px; border-top: 1px solid var(--line);
      background: var(--panel); }
    .composer-row { display: flex; gap: 8px; align-items: center; }
    .composer-row input { flex: 1; border: 1px solid var(--line); border-radius: 6px;
      padding: 9px 11px; }
    .empty { color: var(--muted); padding: 24px; text-align: center; }
    .hidden { display: none !important; }
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 38vh; }
      .bubble { max-width: 92%; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div><h1>Formwork Chat</h1><span class="subtle">会话式 coding agent（experimental）</span></div>
      <div id="currentBadge" class="subtle"></div>
    </header>
    <main>
      <aside>
        <div class="side-tools"><button id="newSession" type="button">New session</button></div>
        <div id="sessionList" class="session-list"><div class="empty">Loading sessions.</div></div>
      </aside>
      <section class="chat">
        <div id="approvalBar" class="approval-bar hidden">
          <div class="grow">
            <div id="approvalQuestion" class="subtle"></div>
            <div id="approvalCommand" class="approval-command"></div>
          </div>
          <button id="approveBtn" type="button">Approve</button>
          <button id="denyBtn" type="button">Deny</button>
        </div>
        <div id="messages" class="messages"><div class="empty">Select or create a session.</div></div>
        <div class="composer">
          <div class="composer-row">
            <input id="messageInput" type="text" placeholder="Message the agent…" autocomplete="off">
            <button id="sendBtn" type="button">Send</button>
          </div>
          <div class="composer-row">
            <input id="steerInput" type="text" placeholder="Steer: redirect the agent…" autocomplete="off">
            <button id="steerBtn" type="button">Steer</button>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const state = { sessions: [], currentId: null, transcript: [], pendingApproval: null, events: null };
    const $ = (id) => document.getElementById(id);
    function escapeHtml(v) {
      return String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    async function fetchJson(url, opts) {
      const res = await fetch(url, opts);
      if (!res.ok) throw new Error(await res.text() || res.status);
      return res.json();
    }
    async function loadSessions() {
      state.sessions = await fetchJson('/api/sessions');
      renderSessions();
      if (!state.currentId && state.sessions.length) await selectSession(state.sessions[0].session_id);
    }
    function renderSessions() {
      const el = $('sessionList');
      el.innerHTML = state.sessions.length ? state.sessions.map((s) => `
        <button class="session-item ${s.session_id === state.currentId ? 'active' : ''}" data-id="${escapeHtml(s.session_id)}">
          <span class="session-title">${escapeHtml(s.title || s.session_id)}</span>
          <span class="session-sub">${escapeHtml(s.project_root)} · ${s.turn_count} turn(s)</span>
        </button>`).join('') : '<div class="empty">No sessions.</div>';
      el.querySelectorAll('[data-id]').forEach((b) => b.addEventListener('click', () => selectSession(b.dataset.id)));
    }
    async function selectSession(id) {
      state.currentId = id; state.pendingApproval = null; renderApproval(); renderSessions();
      $('currentBadge').textContent = 'session: ' + id;
      await loadTranscript(id); subscribe(id);
    }
    async function loadTranscript(id) {
      state.transcript = await fetchJson('/api/sessions/' + encodeURIComponent(id) + '/transcript');
      renderTranscript();
    }
    function renderTranscript() {
      const el = $('messages');
      el.innerHTML = state.transcript.length ? state.transcript.map((m) => `
        <div class="bubble ${escapeHtml(m.role)}"><span class="who">${escapeHtml(m.role)}</span>${escapeHtml(m.content)}</div>`).join('')
        : '<div class="empty">No messages yet.</div>';
      el.scrollTop = el.scrollHeight;
    }
    function subscribe(id) {
      if (state.events) state.events.close();
      state.events = new EventSource('/api/sessions/' + encodeURIComponent(id) + '/events');
      state.events.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === 'turn_waiting_approval') { state.pendingApproval = ev; renderApproval(); }
        if (ev.type === 'turn_done' || ev.type === 'steer') { state.pendingApproval = null; renderApproval(); loadTranscript(id); }
      };
    }
    function renderApproval() {
      const bar = $('approvalBar');
      if (!state.pendingApproval) { bar.classList.add('hidden'); return; }
      bar.classList.remove('hidden');
      $('approvalQuestion').textContent = state.pendingApproval.approval_question || 'Approve this action?';
      $('approvalCommand').textContent = state.pendingApproval.pending_command || '(no command)';
    }
    function appendLocal(role, content) { state.transcript.push({ role, content }); renderTranscript(); }
    async function sendMessage() {
      const input = $('messageInput'); const text = input.value.trim();
      if (!text || !state.currentId) return;
      input.value = ''; appendLocal('user', text);
      const result = await fetchJson('/api/sessions/' + encodeURIComponent(state.currentId) + '/messages',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
      if (result.pending_command) { state.pendingApproval = result; renderApproval(); }
      else appendLocal('assistant', result.assistant_reply);
      loadTranscript(state.currentId);
    }
    async function steerTurn() {
      const input = $('steerInput'); const text = input.value.trim();
      if (!text || !state.currentId) return;
      input.value = '';
      await fetchJson('/api/sessions/' + encodeURIComponent(state.currentId) + '/steer',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
      loadTranscript(state.currentId);
    }
    async function resolveApproval(verb) {
      const p = state.pendingApproval; if (!p || !state.currentId) return;
      state.pendingApproval = null; renderApproval();
      const url = '/api/sessions/' + encodeURIComponent(state.currentId) + '/runs/' + encodeURIComponent(p.run_id) + '/' + verb;
      const result = await fetchJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
      appendLocal('assistant', result.assistant_reply);
      loadTranscript(state.currentId);
    }
    async function newSession() {
      const root = window.prompt('Project directory (blank = server cwd):', '');
      if (root === null) return;
      const created = await fetchJson('/api/sessions',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_root: root }) });
      await loadSessions();
    }
    $('sendBtn').addEventListener('click', () => sendMessage().catch((e) => alert(e.message)));
    $('messageInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMessage().catch((e) => alert(e.message)); });
    $('steerBtn').addEventListener('click', () => steerTurn().catch((e) => alert(e.message)));
    $('approveBtn').addEventListener('click', () => resolveApproval('approve'));
    $('denyBtn').addEventListener('click', () => resolveApproval('deny'));
    $('newSession').addEventListener('click', () => newSession().catch((e) => alert(e.message)));
    loadSessions().catch((e) => { $('sessionList').innerHTML = '<div class="empty">' + escapeHtml(e.message) + '</div>'; });
  </script>
</body>
</html>"""
