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
    """Transcript rows enriched with recoverable, user-facing run progress.

    Conversation rows are committed only after a turn reaches a terminal state.
    A browser reload during execution therefore cannot rely on transcript.jsonl
    alone.  We project public progress from each run trace and synthesize an
    ``in_progress`` user row for a run that has started but is not committed yet.
    Raw commands and tool results never cross this boundary.
    """
    messages = [message.to_dict() for message in store.read_transcript(session_id)]
    by_run: dict[str, dict[str, Any]] = {}
    for row in messages:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id:
            by_run[run_id] = row

    run_root = store.session_runs_dir(session_id)
    if not run_root.exists():
        return messages

    next_seq = max((int(row.get("seq") or 0) for row in messages), default=0)
    for run_dir in sorted((child for child in run_root.iterdir() if child.is_dir()), key=lambda p: p.name):
        run_id = run_dir.name
        trace_path = run_dir / "trace.jsonl"
        activities: list[str] = []
        goal: str | None = None
        status: str | None = None
        if trace_path.is_file():
            try:
                for line in trace_path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    goal = goal or (str(event.get("goal")) if event.get("goal") else None)
                    status = str(event.get("status")) if event.get("status") else status
                    projected = progress_event(event)
                    label = projected.get("label") if projected else None
                    if isinstance(label, str) and label and label not in activities:
                        activities.append(label)
            except OSError:
                pass
        state_path = run_dir / "state.json"
        if state_path.is_file():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(state_data, dict):
                    goal = goal or (str(state_data.get("goal")) if state_data.get("goal") else None)
                    status = str(state_data.get("status")) if state_data.get("status") else status
            except (OSError, json.JSONDecodeError):
                pass
        row = by_run.get(run_id)
        if row is not None:
            if activities:
                row["activities"] = activities
            if status:
                row["run_status"] = status
            continue
        if not goal:
            continue
        next_seq += 1
        messages.append(
            {
                "seq": next_seq,
                "run_id": run_id,
                "role": "user",
                "content": goal,
                "activities": activities,
                "run_status": status or "running",
                "in_progress": status not in {"completed", "failed", "interrupted"},
            }
        )
    messages.sort(key=lambda row: int(row.get("seq") or 0))
    return messages


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
    *,
    on_text_delta: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one turn against a *deferred* engine and shape it as an SSE event."""
    return _turn_event(
        engine_factory().submit_turn(
            session_id,
            message,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
        )
    )


def resolve_approval(
    engine_factory: EngineFactory,
    session_id: str,
    run_id: str,
    decision: str,
    *,
    on_text_delta: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Resume a paused turn with an approve/deny decision."""
    return _turn_event(
        engine_factory().resolve_turn(
            session_id,
            run_id,
            decision,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
        )
    )


def progress_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Project verbose trace events into a small user-facing activity update."""
    if event.get("event") == "action_parsed":
        action = event.get("action")
        if isinstance(action, dict):
            progress = str(action.get("progress") or action.get("purpose") or "").strip()
            if progress:
                return {
                    "type": "activity",
                    "label": progress,
                    "event": "agent_progress",
                    "run_id": event.get("run_id"),
                }
        return None
    names = {
        "context_compacted": "正在压缩上下文",
        "semantic_compaction_started": "正在生成上下文摘要",
        "semantic_compaction_finished": "上下文摘要已生成",
        "approval_requested": "等待你的审批",
        "approval_resolved": "审批结果已处理",
        "verification_started": "正在验证结果",
        "verification_finished": "结果验证完成",
        "working_memory_captured": "已保存本轮工作记忆",
        "working_memory_injected": "正在载入历史工作记忆",
        "memory_reference_captured": "已记录可复用代码事实",
        "run_failed": "任务执行失败",
        "run_interrupted": "任务已暂停",
    }
    label = names.get(str(event.get("event")))
    if label is None:
        return None
    projected = {
        "type": "activity",
        "label": label,
        "event": str(event.get("event")),
        "sequence": event.get("sequence"),
    }
    # Trace events carry the run id once a turn has started.  Keeping it on the
    # projected event lets the browser attach progress to the correct task card
    # when a session has more than one turn.
    if event.get("run_id") is not None:
        projected["run_id"] = event["run_id"]
    return projected


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
    # Mount the existing ops handler into this server so Chat and Ops share one
    # origin and one browser shell. The two handlers keep their own registries.
    from minicc.server.ops import JobRegistry, OpsBroker
    from minicc.server.ops import _handler_for as ops_handler_for

    ops_handler = ops_handler_for(JobRegistry(), OpsBroker())
    handler = _handler_for(store, engine_factory, broker, ops_handler=ops_handler)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"miniCC chat (experimental): http://{host}:{port}")
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
    *,
    ops_handler: type[BaseHTTPRequestHandler] | None = None,
) -> type[BaseHTTPRequestHandler]:
    class ChatHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            segments = self._segments()
            if ops_handler is not None and (
                segments == ["ops"]
                or (len(segments) > 1 and segments[0] == "ops")
                or (len(segments) > 1 and segments[0] == "api" and segments[1] in {"config", "runs", "eval", "replay", "models"})
            ):
                if segments == ["ops"]:
                    self._send_html(render_ops_shell())
                    return
                if segments and segments[0] == "ops":
                    self.path = self.path[len("/ops") :] or "/"
                    return ops_handler.do_GET(self)
                if segments[0] == "api" and segments[1] in {"config", "runs", "eval", "replay", "models"}:
                    return ops_handler.do_GET(self)
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
            if ops_handler is not None and segments and segments[0] == "api" and len(segments) > 1 and segments[1] in {"runs", "eval", "replay", "models"}:
                return ops_handler.do_POST(self)
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
                self._run_and_respond(
                    segments[2],
                    lambda: execute_turn(
                        engine_factory,
                        segments[2],
                        message,
                        on_text_delta=lambda text: broker.publish(
                            segments[2], {"type": "text_delta", "text": text}
                        ),
                        on_progress=lambda event: (
                            broker.publish(segments[2], activity)
                            if (activity := progress_event(event)) is not None
                            else None
                        ),
                    ),
                )
                return
            if self._is(segments, ["api", "sessions"], tail=2) and segments[3] == "steer":
                message = str(body.get("message") or "").strip()
                if not message:
                    self._send_error(400, "message is required")
                    return
                broker.publish(segments[2], {"type": "steer", "message": message})
                self._run_and_respond(
                    segments[2],
                    lambda: execute_turn(
                        engine_factory,
                        segments[2],
                        message,
                        on_text_delta=lambda text: broker.publish(
                            segments[2], {"type": "text_delta", "text": text}
                        ),
                        on_progress=lambda event: (
                            broker.publish(segments[2], activity)
                            if (activity := progress_event(event)) is not None
                            else None
                        ),
                    ),
                )
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
                event = resolve_approval(
                    engine_factory,
                    session_id,
                    run_id,
                    decision,
                    on_text_delta=lambda text: broker.publish(
                        session_id, {"type": "text_delta", "text": text}
                    ),
                    on_progress=lambda event: (
                        broker.publish(session_id, activity)
                        if (activity := progress_event(event)) is not None
                        else None
                    ),
                )
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


def render_ops_shell() -> str:
    """Render the existing ops console when it is mounted in the chat shell."""
    from minicc.server.ops import render_ops_index

    return render_ops_index()


_CHAT_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>miniCC Chat</title>
  <style>
    :root { --bg:#fff; --panel:#fff; --ink:#202124; --muted:#70757a; --line:#e5e7eb;
      --accent:#1a73e8; --accent-soft:#f1f3f4; --accent-dark:#1557b0; --danger:#b42318;
      --shadow:0 1px 3px rgba(60,64,67,.18); }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--ink);
      font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    button, input { font: inherit; }
    button { border:1px solid transparent; background:var(--panel); color:var(--ink); border-radius:8px;
      padding:8px 12px; cursor:pointer; transition:background .15s,border-color .15s,opacity .15s; }
    button:hover:not(:disabled) { border-color:transparent; background:#f1f3f4; }
    button:disabled { cursor:not-allowed; opacity:.55; }
    button.primary { background:#f1f3f4; border-color:transparent; color:var(--ink); font-weight:600; }
    button.primary:hover:not(:disabled) { background:#e8eaed; }
    .app { display: grid; grid-template-rows: auto 1fr; min-height: 100vh; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px;
      padding:18px 22px 14px; border-bottom:1px solid var(--line); background:var(--panel); }
    h1 { margin:0; font-size:19px; font-weight:500; letter-spacing:0; }
    .brand-mark { color:#1a73e8; font-size:22px; vertical-align:-2px; margin-right:5px; }
    .subtle { color: var(--muted); font-size: 12px; }
    main { display:grid; grid-template-columns:280px minmax(0,1fr); min-height:0; }
    aside { border-right:1px solid var(--line); background:var(--panel);
      display: grid; grid-template-rows: auto 1fr; min-height: 0; }
    .side-tools { display:grid; gap:4px; padding:14px 12px 10px; border-bottom:1px solid var(--line); }
    .nav-item { width:100%; text-align:left; border-color:transparent; box-shadow:none; border-radius:22px; padding:9px 14px; }
    .nav-item.active { background:#f1f3f4; border-color:transparent; font-weight:600; }
    .nav-icon { display:inline-grid; place-items:center; width:22px; margin-right:5px; font-size:16px; }
    .sidebar-heading { color:var(--muted); font-size:12px; padding:18px 8px 6px; }
    .session-list { overflow:auto; padding:4px 12px 14px; }
    .session-item { width:100%; text-align:left; margin-bottom:3px; padding:10px 12px;
      display:grid; gap:3px; border-color:transparent; box-shadow:none; }
    .session-item.active { border-color:transparent; background:#f1f3f4; }
    .session-title { font-weight: 700; overflow-wrap: anywhere; }
    .session-sub { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .chat { grid-column:2; grid-row:1; display:grid; grid-template-rows:auto 1fr auto; min-height:0; background:#fff; }
    .ops-view { grid-column:2; grid-row:1; min-width:0; min-height:0; background:var(--bg); }
    .ops-view iframe { width:100%; height:100%; min-height:720px; border:0; display:block; background:var(--bg); }
    .approval-bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
      margin:14px auto 0; width:min(920px,calc(100% - 40px)); padding:12px 16px; border:1px solid #f1dfb5; border-radius:16px; background:#fffaf0; }
    .approval-bar .grow { flex: 1; min-width: 200px; }
    .approval-command { font-family: monospace; color: var(--danger); }
    .task-card { width:min(900px,100%); margin:2px 0 2px auto; padding:0 2px; color:#4b5563; }
    .task-card details { border:1px solid var(--line); border-radius:10px; background:#fbfcfd; box-shadow:var(--shadow); }
    .task-card summary { display:flex; align-items:center; gap:8px; min-height:42px; padding:10px 13px;
      cursor:pointer; list-style:none; font-size:13px; font-weight:600; color:var(--ink); }
    .task-card summary::-webkit-details-marker { display:none; }
    .task-card summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:14px; }
    .task-card details:not([open]) summary::after { content:'›'; }
    .task-state { width:8px; height:8px; flex:0 0 8px; border-radius:50%; background:var(--accent); }
    .task-card.running .task-state { animation:pulse 1.2s ease-in-out infinite; }
    .task-card.completed .task-state { background:#188038; }
    .task-card.waiting .task-state { background:#f29900; }
    .task-card.failed .task-state { background:var(--danger); }
    .task-meta { color:var(--muted); font-size:12px; font-weight:400; }
    .task-steps { display:grid; gap:6px; margin:0 13px 12px 29px; padding-top:2px; }
    .task-step { display:flex; align-items:baseline; gap:7px; color:#5f6368; font-size:12px; }
    .task-step::before { content:'✓'; color:#188038; font-weight:700; }
    .task-step.current { color:var(--ink); font-weight:600; }
    .task-step.current::before { content:'·'; color:var(--accent); }
    @keyframes pulse { 0%,100% { opacity:.35; } 50% { opacity:1; } }
    .messages { overflow:auto; padding:30px clamp(16px,6vw,92px); display:grid; gap:14px; align-content:start; }
    .bubble { max-width:min(760px,82%); padding:11px 14px; border-radius:8px; white-space:pre-wrap;
      overflow-wrap:anywhere; box-shadow:var(--shadow); }
    .bubble.user { justify-self:end; background:var(--accent-soft); border:1px solid #c8e7e2; }
    .bubble.assistant { justify-self:start; background:#fff; border:1px solid var(--line); }
    .bubble .who { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
    .composer { display:grid; gap:8px; padding:16px 18px 22px; border-top:0; background:var(--panel); }
    .composer-row { display:flex; gap:8px; align-items:center; max-width:900px; width:100%; margin:0 auto; }
    .composer-row input { flex:1; min-width:0; border:1px solid #dadce0; border-radius:26px;
      padding:13px 18px; outline:none; background:#fff; box-shadow:var(--shadow); }
    .composer-row input:focus { border-color:#c7d5ef; box-shadow:0 1px 3px rgba(60,64,67,.18),0 0 0 3px rgba(26,115,232,.10); }
    .composer-row:nth-of-type(2) button { border-radius:22px; padding-inline:18px; }
    .composer-row:nth-of-type(3) input { box-shadow:none; border-color:transparent; background:#f8f9fa; padding:9px 14px; }
    .composer-row:nth-of-type(3) button { border-radius:20px; color:var(--muted); }
    .empty { color:var(--muted); padding:28px 16px; text-align:center; }
    .empty-state { min-height:100%; display:grid; place-content:center; justify-items:center; gap:10px; text-align:center; padding:48px 20px; }
    .empty-state .welcome-mark { color:#1a73e8; font-size:34px; line-height:1; }
    .empty-state h2 { margin:0; font-size:36px; font-weight:400; letter-spacing:0; }
    .empty-state p { margin:0; color:var(--muted); max-width:420px; }
    .status-line { min-height:20px; max-width:980px; width:100%; margin:0 auto; color:var(--muted); font-size:12px; }
    .hidden { display: none !important; }
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      header { padding:12px 14px; }
      main { grid-template-columns:1fr; grid-template-rows:auto 1fr; }
      aside { border-right:0; border-bottom:1px solid var(--line); max-height:38vh; }
      .chat, .ops-view { grid-column:1; grid-row:2; }
      .bubble { max-width:92%; }
      .messages { padding:18px 12px; }
      .task-card { width:100%; }
      .composer { padding:10px 12px 12px; }
      .composer-row { align-items:stretch; }
      .composer-row button { flex:0 0 auto; }
      .ops-view iframe { min-height:900px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div><h1><span class="brand-mark">✦</span>miniCC</h1><span class="subtle">会话式 coding agent</span></div>
      <div id="currentBadge" class="subtle">未选择会话</div>
    </header>
    <main>
      <aside>
        <div class="side-tools">
          <button id="newSession" class="primary" type="button" title="选择工作目录"><span class="nav-icon">＋</span>新建工作区</button>
          <button id="viewChat" class="nav-item active" type="button"><span class="nav-icon">⌁</span>对话</button>
          <button id="viewOps" class="nav-item" type="button"><span class="nav-icon">▦</span>Ops 控制台</button>
          <div class="sidebar-heading">最近会话</div>
        </div>
        <div id="sessionList" class="session-list"><div class="empty">Loading sessions.</div></div>
      </aside>
      <section id="chatView" class="chat">
        <div id="approvalBar" class="approval-bar hidden">
          <div class="grow">
            <div id="approvalQuestion" class="subtle"></div>
            <div id="approvalCommand" class="approval-command"></div>
          </div>
          <button id="approveBtn" class="primary" type="button">允许执行</button>
          <button id="denyBtn" type="button">拒绝</button>
        </div>
        <div id="messages" class="messages"><div class="empty-state"><div class="welcome-mark">✦</div><h2>今天想完成什么？</h2><p>选择一个工作目录，开始和 miniCC 协作。</p></div></div>
        <div class="composer">
          <div id="statusLine" class="status-line" aria-live="polite"></div>
          <div class="composer-row">
            <input id="messageInput" type="text" placeholder="输入任务，按 Enter 发送" autocomplete="off">
            <button id="sendBtn" class="primary" type="button">发送</button>
          </div>
          <div class="composer-row">
            <input id="steerInput" type="text" placeholder="需要改变方向时，输入 steer 指令" autocomplete="off">
            <button id="steerBtn" type="button">调整方向</button>
          </div>
        </div>
      </section>
      <section id="opsView" class="ops-view hidden" aria-label="Ops 控制台">
        <iframe title="Ops 控制台" src="/ops"></iframe>
      </section>
    </main>
  </div>
  <script>
    const state = { sessions: [], currentId: null, transcript: [], pendingApproval: null, events: null, busy: false, tasks: new Map(), activeTaskId: null };
    const $ = (id) => document.getElementById(id);
    function showView(view) {
      const isChat = view === 'chat';
      $('chatView').classList.toggle('hidden', !isChat);
      $('opsView').classList.toggle('hidden', isChat);
      $('viewChat').classList.toggle('active', isChat);
      $('viewOps').classList.toggle('active', !isChat);
      $('currentBadge').textContent = isChat
        ? (state.currentId ? '当前会话 · ' + state.currentId : '未选择会话')
        : 'Ops 控制台';
      if (isChat && state.currentId) loadTranscript(state.currentId).catch(() => {});
    }
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
          <span class="session-sub">${escapeHtml(s.project_root)} · ${s.turn_count} 轮对话</span>
        </button>`).join('') : '<div class="empty">No sessions.</div>';
      el.querySelectorAll('[data-id]').forEach((b) => b.addEventListener('click', () => selectSession(b.dataset.id)));
    }
    async function selectSession(id) {
      state.currentId = id; state.pendingApproval = null; state.tasks = new Map(); state.activeTaskId = null;
      renderApproval(); renderSessions();
      $('currentBadge').textContent = '当前会话 · ' + id;
      setStatus('正在加载会话…');
      await loadTranscript(id); subscribe(id);
      setStatus('就绪');
    }
    async function loadTranscript(id) {
      const previous = state.transcript;
      const remote = await fetchJson('/api/sessions/' + encodeURIComponent(id) + '/transcript');
      mergeTranscript(remote, previous);
    }
    function mergeTranscript(remote, previous = state.transcript) {
      const optimistic = previous.filter((row) => row.task_id && !row.run_id);
      const remoteKeys = new Set(remote.map((row) => `${row.role}:${row.content}`));
      state.transcript = remote.concat(
        optimistic.filter((row) => !remoteKeys.has(`${row.role}:${row.content}`))
      );
      ensureTranscriptTasks();
      renderTranscript();
    }
    function ensureTranscriptTasks() {
      state.transcript.filter((m) => m.role === 'user' && m.run_id).forEach((m) => {
        let task = state.tasks.get(m.run_id);
        if (!task) {
          task = { id: m.run_id, runId: m.run_id, question: m.content, steps: [], status: 'completed' };
          state.tasks.set(m.run_id, task);
        }
        task.question = m.content;
        (m.activities || []).forEach((label) => {
          if (typeof label === 'string' && !task.steps.includes(label)) task.steps.push(label);
        });
        if (m.in_progress || m.run_status === 'running' || m.run_status === 'waiting_approval') {
          task.status = m.run_status === 'waiting_approval' ? 'waiting' : 'running';
        } else if (m.run_status === 'failed') {
          task.status = 'failed';
        } else if (m.run_status) {
          task.status = 'completed';
        }
      });
    }
    function renderTranscript() {
      const el = $('messages');
      el.innerHTML = state.transcript.length ? state.transcript.map((m) => {
        const bubble = `<div class="bubble ${escapeHtml(m.role)}"><span class="who">${m.role === 'user' ? '你' : 'miniCC'}</span>${escapeHtml(m.content)}</div>`;
        if (m.role !== 'user') return bubble;
        const task = (m.task_id && state.tasks.get(m.task_id)) || (m.run_id && state.tasks.get(m.run_id));
        if (!task) return bubble;
        return bubble + renderTask(task);
      }).join('')
        : '<div class="empty-state"><div class="welcome-mark">✦</div><h2>今天想完成什么？</h2><p>告诉 agent 你的目标，它会在当前工作目录中协助你完成。</p></div>';
      el.scrollTop = el.scrollHeight;
    }
    function subscribe(id) {
      if (state.events) state.events.close();
      state.events = new EventSource('/api/sessions/' + encodeURIComponent(id) + '/events');
      state.events.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === 'snapshot' && Array.isArray(ev.transcript)) {
          mergeTranscript(ev.transcript);
        }
        if (ev.type === 'activity') addActivity(ev);
        if (ev.type === 'turn_waiting_approval') {
          state.pendingApproval = ev; finishTask(ev, 'waiting'); renderApproval(); setBusy(false); setStatus('等待你确认后继续');
        }
        if (ev.type === 'turn_done') {
          state.pendingApproval = null; finishTask(ev, ev.status === 'completed' ? 'completed' : (ev.status || 'completed'));
          renderApproval(); loadTranscript(id); setBusy(false); setStatus('就绪');
        }
      };
      state.events.onerror = () => { if (state.currentId === id) setStatus('实时连接暂时中断，页面仍可继续操作'); };
    }
    function renderApproval() {
      const bar = $('approvalBar');
      if (!state.pendingApproval) { bar.classList.add('hidden'); return; }
      bar.classList.remove('hidden');
      $('approvalQuestion').textContent = state.pendingApproval.approval_question || '需要你的确认';
      $('approvalCommand').textContent = state.pendingApproval.pending_command || '(未提供命令)';
    }
    function createTask(question) {
      const id = 'local-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      const task = { id, runId: null, question, steps: [], status: 'running' };
      state.tasks.set(id, task); state.activeTaskId = id; renderTranscript();
      return task;
    }
    function taskForEvent(ev) {
      let task = ev.run_id ? state.tasks.get(ev.run_id) : null;
      if (!task && state.activeTaskId) task = state.tasks.get(state.activeTaskId);
      if (!task) task = createTask('当前任务');
      if (ev.run_id) bindTask(task, ev.run_id);
      state.activeTaskId = task.id;
      return task;
    }
    function bindTask(task, runId) {
      const localId = task.id;
      task.runId = runId; task.id = runId;
      // Keep the local id as an alias until the transcript reload replaces the
      // optimistic user row with its persisted run_id.
      state.tasks.set(localId, task); state.tasks.set(runId, task);
    }
    function addActivity(ev) {
      const task = taskForEvent(ev); const label = String(ev.label || '正在处理');
      if (!task.steps.includes(label)) task.steps.push(label);
      task.status = 'running';
      renderTranscript(); setStatus(label + '…');
    }
    function finishTask(ev, status) {
      const task = taskForEvent(ev); task.status = status === 'waiting_approval' || status === 'waiting' ? 'waiting' : (status === 'failed' ? 'failed' : 'completed');
      if (task.status !== 'waiting') state.activeTaskId = null;
      renderTranscript();
    }
    function renderTask(task) {
      const status = task.status || 'running';
      const title = status === 'running' ? 'Agent 正在工作' : status === 'waiting' ? '等待你的审批' : status === 'failed' ? '任务失败' : '已完成';
      const meta = status === 'running' ? '' : ` · ${task.steps.length || 1} 个步骤`;
      const steps = task.steps.length ? `<div class="task-steps">${task.steps.map((step, i) => `<div class="task-step ${i === task.steps.length - 1 && status === 'running' ? 'current' : ''}">${escapeHtml(step)}</div>`).join('')}</div>` : '';
      return `<div class="task-card ${status}"><details${status === 'running' || status === 'waiting' ? ' open' : ''}><summary><span class="task-state"></span><span>${title}</span><span class="task-meta">${meta}</span></summary>${steps}</details></div>`;
    }
    function setStatus(text) { $('statusLine').textContent = text || ''; }
    function setBusy(busy) {
      state.busy = busy;
      $('sendBtn').disabled = busy; $('steerBtn').disabled = busy;
      $('messageInput').disabled = busy; $('steerInput').disabled = busy;
      if (busy) setStatus('agent 正在处理…');
    }
    function appendLocal(role, content, taskId = null) { state.transcript.push({ role, content, task_id: taskId }); renderTranscript(); }
    async function sendMessage() {
      const input = $('messageInput'); const text = input.value.trim();
      if (!text || !state.currentId || state.busy) return;
      input.value = ''; const task = createTask(text); appendLocal('user', text, task.id); setBusy(true);
      try {
        const result = await fetchJson('/api/sessions/' + encodeURIComponent(state.currentId) + '/messages',
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
        if (result.pending_command) { bindTask(task, result.run_id); state.activeTaskId = task.id; state.pendingApproval = result; finishTask(result, 'waiting'); renderApproval(); setBusy(false); setStatus('等待你确认后继续'); }
        await loadTranscript(state.currentId);
        if (!result.pending_command) { finishTask(result, result.status); setBusy(false); setStatus('就绪'); }
      } catch (e) { setBusy(false); setStatus('发送失败：' + e.message); throw e; }
    }
    async function steerTurn() {
      const input = $('steerInput'); const text = input.value.trim();
      if (!text || !state.currentId || state.busy) return;
      input.value = '';
      const task = createTask(text); appendLocal('user', text, task.id);
      setBusy(true);
      try {
        const result = await fetchJson('/api/sessions/' + encodeURIComponent(state.currentId) + '/steer',
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
        bindTask(task, result.run_id);
        finishTask(result, result.status); await loadTranscript(state.currentId); setBusy(false); setStatus('方向已调整');
      } catch (e) { setBusy(false); setStatus('调整失败：' + e.message); throw e; }
    }
    async function resolveApproval(verb) {
      const p = state.pendingApproval; if (!p || !state.currentId) return;
      state.pendingApproval = null; renderApproval();
      const url = '/api/sessions/' + encodeURIComponent(state.currentId) + '/runs/' + encodeURIComponent(p.run_id) + '/' + verb;
      setBusy(true);
      try {
        const result = await fetchJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        finishTask(result, result.status); await loadTranscript(state.currentId); setBusy(false); setStatus('就绪');
      } catch (e) { setBusy(false); setStatus('审批处理失败：' + e.message); alert(e.message); }
    }
    async function newSession() {
      const root = window.prompt('项目目录（留空使用服务端当前目录）：', '');
      if (root === null) return;
      const created = await fetchJson('/api/sessions',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_root: root }) });
      await loadSessions();
    }
    $('sendBtn').addEventListener('click', () => sendMessage().catch((e) => alert(e.message)));
    $('messageInput').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage().catch((e) => alert(e.message)); } });
    $('steerBtn').addEventListener('click', () => steerTurn().catch((e) => alert(e.message)));
    $('approveBtn').addEventListener('click', () => resolveApproval('approve'));
    $('denyBtn').addEventListener('click', () => resolveApproval('deny'));
    $('newSession').addEventListener('click', () => newSession().catch((e) => alert(e.message)));
    $('viewChat').addEventListener('click', () => showView('chat'));
    $('viewOps').addEventListener('click', () => showView('ops'));
    loadSessions().then(() => setStatus('就绪')).catch((e) => { $('sessionList').innerHTML = '<div class="empty">加载失败：' + escapeHtml(e.message) + '</div>'; setStatus('无法连接服务'); });
  </script>
</body>
</html>"""
