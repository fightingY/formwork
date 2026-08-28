"""Web ops console: run / eval / replay / model-discovery over HTTP + SSE (experimental).

Pure-stdlib ``ThreadingHTTPServer`` + SSE, mirroring ``server/chat.py`` and
``server/app.py`` so the project keeps its zero-third-party-dependency rule.
Where the CLI already has a battle-tested command (``run``, ``resume``,
``approve``, ``deny``, ``eval``, ``replay create``, ``replay run``), this
module drives that exact command via ``minicc.cli`` in a background thread
instead of re-implementing loop/provider/executor wiring. A launched job's
``run_id`` is not known until the CLI function creates it internally, so we
detect it the same way ``replay_run_command`` already does for fresh replay:
diff ``.minicc/runs`` before/after starting the job.

Endpoints:

    GET  /                             single-page ops console
    GET  /api/config                   provider summary (no secrets)
    POST /api/models/discover          {route?, probe_key?} -> model list
    GET  /api/runs                     list run/resume jobs
    POST /api/runs                     launch `run`  {goal, execute_local, ...}
    GET  /api/runs/<job_id>            job detail
    GET  /api/runs/<job_id>/events     SSE: run_detected / trace_event / job_status
    POST /api/runs/<job_id>/approve    approve pending action, auto-resume
    POST /api/runs/<job_id>/deny       {reason?} deny pending action, auto-resume
    GET  /api/eval/cases               {path} -> discovered case names
    GET  /api/eval                     list eval jobs
    POST /api/eval                     launch `eval` {path, execute_local, repeat, case_names, ...}
    GET  /api/eval/<job_id>            job detail
    GET  /api/eval/<job_id>/events     SSE
    POST /api/replay/create            synchronous {run_id, output_dir?, overwrite?}
    GET  /api/replay                   list replay-run jobs
    POST /api/replay/run               launch `replay run` {case, fresh?, ...}
    GET  /api/replay/<job_id>          job detail
    GET  /api/replay/<job_id>/events   SSE

Never echoes a resolved provider API key back to a client; only its sha256
fingerprint (``minicc.cli._secret_fingerprint``), matching the CLI convention.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

JobKind = str
JobStatus = str

_TERMINAL_STATUSES = {"completed", "failed"}


@dataclass
class Job:
    """One background CLI invocation tracked for the web console."""

    job_id: str
    kind: JobKind
    request: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = "queued"
    run_ids: list[str] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    execute_local: bool = True


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "run_ids": list(job.run_ids),
        "error": job.error,
        "created_at": job.created_at,
        "request": job.request,
    }


class JobRegistry:
    """Thread-safe in-memory job table. Not persisted -- restart loses history."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, kind: JobKind, request: dict[str, Any], *, execute_local: bool) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind, request=request, execute_local=execute_local)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, kind: JobKind) -> list[Job]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.kind == kind]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)


# --- SSE broker ----------------------------------------------------------

SSE_HEARTBEAT_SECONDS = 15


class OpsBroker:
    """Thread-safe pub/sub keyed by job_id, mirroring ``server/chat.py``'s ChatBroker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, job_id: str) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, job_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            bucket = self._subscribers.get(job_id)
            if bucket is None:
                return
            if subscriber in bucket:
                bucket.remove(subscriber)
            if not bucket:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(job_id, []))
        for subscriber in targets:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                continue


# --- run-directory discovery & trace tailing ------------------------------


def _existing_run_ids(runs_root: Path) -> set[str]:
    if not runs_root.is_dir():
        return set()
    return {p.name for p in runs_root.iterdir() if p.is_dir() and (p / "state.json").is_file()}


def _watch_runs(
    job: Job,
    runs_root: Path,
    before_ids: set[str],
    broker: OpsBroker,
    *,
    seed_run_ids: set[str] | None = None,
) -> None:
    """Poll ``runs_root`` for run dirs created by this job and tail their traces.

    Runs in its own thread alongside the blocking CLI call. Detection mirrors
    ``replay_run_command``'s own before/after diff technique (cli.py) rather
    than inventing a new run-id channel. Stops once the job has reached a
    terminal status and one final drain pass finds nothing new.

    ``seed_run_ids`` is for jobs (resume/approve/deny) whose run id is already
    known up front: it is added to ``job.run_ids`` immediately and its trace
    offset starts at the current file size, so only lines appended *after*
    the job started are streamed instead of replaying history already shown.
    """
    offsets: dict[str, int] = {}
    seen_ids: set[str] = set(before_ids)
    idle_after_terminal = False
    if seed_run_ids:
        for run_id in seed_run_ids:
            seen_ids.add(run_id)
            job.run_ids.append(run_id)
            trace_path = runs_root / run_id / "trace.jsonl"
            if trace_path.is_file():
                try:
                    offsets[run_id] = trace_path.stat().st_size
                except OSError:
                    pass
    while True:
        current_ids = _existing_run_ids(runs_root)
        new_ids = sorted(current_ids - seen_ids)
        for run_id in new_ids:
            seen_ids.add(run_id)
            job.run_ids.append(run_id)
            broker.publish(job.job_id, {"type": "run_detected", "run_id": run_id})

        progressed = False
        for run_id in list(job.run_ids):
            trace_path = runs_root / run_id / "trace.jsonl"
            if not trace_path.is_file():
                continue
            offset = offsets.get(run_id, 0)
            try:
                with trace_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offsets[run_id] = handle.tell()
            except OSError:
                continue
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    progressed = True
                    broker.publish(job.job_id, {"type": "trace_event", "run_id": run_id, "event": event})

        if job.status in _TERMINAL_STATUSES:
            if idle_after_terminal and not progressed and not new_ids:
                break
            idle_after_terminal = True
        time.sleep(0.4)
    broker.publish(job.job_id, {"type": "job_status", "status": job.status, "error": job.error})


# --- job launchers ---------------------------------------------------------
#
# Each launcher builds the same argparse.Namespace the real CLI subcommand
# would receive, then runs that command function in a background thread.  We
# reuse minicc.cli's own command functions rather than re-implementing
# provider/executor/loop wiring, per this module's docstring.


def _run_namespace(payload: dict[str, Any]) -> argparse.Namespace:
    goal = str(payload.get("goal") or "").strip()
    verify_commands = [str(c) for c in (payload.get("verify_command") or []) if str(c).strip()]
    return argparse.Namespace(
        goal=goal,
        milestone=payload.get("milestone"),
        source_dir=Path(str(payload["source_dir"])) if payload.get("source_dir") else None,
        execute_local=bool(payload.get("execute_local", True)),
        verify_command=verify_commands,
        verification_timeout_sec=int(payload.get("verification_timeout_sec") or 120),
        no_workspace_copy=bool(payload.get("no_workspace_copy", False)),
        docker_image=payload.get("docker_image"),
        stream=None,
        profile=payload.get("profile"),
        follow_up_from=payload.get("follow_up_from"),
        interrupt_after_steps=payload.get("interrupt_after_steps"),
    )


def _eval_namespace(payload: dict[str, Any]) -> argparse.Namespace:
    case_names = payload.get("case_names")
    return argparse.Namespace(
        path=str(payload.get("path") or "eval_cases"),
        milestone=payload.get("milestone"),
        execute_local=bool(payload.get("execute_local", True)),
        repeat=int(payload.get("repeat") or 1),
        output_dir=Path(str(payload["output_dir"])) if payload.get("output_dir") else None,
        case_names=[str(c) for c in case_names] if case_names else None,
        release_gate=False,
        context_variant=None,
        cache_variant=None,
        cache_sequence_id=None,
        execution_order=None,
        guidance_variant=None,
        guidance_sequence_id=None,
        guidance_execution_order=None,
        guidance_feedback_path="guidance/feedback_rules.jsonl",
    )


def _replay_run_namespace(payload: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        case=Path(str(payload.get("case") or "")),
        fresh=bool(payload.get("fresh", False)),
        execute_local=bool(payload.get("execute_local", True)),
        docker_image=payload.get("docker_image"),
        profile=payload.get("profile"),
        verify_command=[str(c) for c in (payload.get("verify_command") or []) if str(c).strip()],
        verification_timeout_sec=int(payload.get("verification_timeout_sec") or 120),
        output_dir=Path(str(payload["output_dir"])) if payload.get("output_dir") else None,
        json_output=False,
    )


def launch_job(
    registry: JobRegistry,
    broker: OpsBroker,
    kind: JobKind,
    payload: dict[str, Any],
    command: Callable[[argparse.Namespace], int],
    build_namespace: Callable[[dict[str, Any]], argparse.Namespace],
    *,
    runs_root: Path,
) -> Job:
    """Create a job, then run ``command`` against it in a background thread.

    A second thread (``_watch_runs``) starts concurrently to detect the run
    directory the command creates and stream its trace over SSE. Any
    exception raised by the CLI command function is captured onto the job
    rather than propagated, since this runs off the request thread.
    """
    execute_local = bool(payload.get("execute_local", True))
    job = registry.create(kind, payload, execute_local=execute_local)
    before_ids = _existing_run_ids(runs_root)

    def _worker() -> None:
        job.status = "running"
        try:
            namespace = build_namespace(payload)
            return_code = command(namespace)
            job.status = "completed" if return_code == 0 else "failed"
            if return_code != 0:
                job.error = f"command exited with status {return_code}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the client, not swallowed
            job.status = "failed"
            job.error = str(exc)

    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(
        target=_watch_runs, args=(job, runs_root, before_ids, broker), daemon=True
    ).start()
    return job


def launch_resume_like_job(
    registry: JobRegistry,
    broker: OpsBroker,
    kind: JobKind,
    payload: dict[str, Any],
    run_id: str,
    step: Callable[[], int],
    *,
    runs_root: Path,
) -> Job:
    """Launch a job whose run id is already known (resume/approve+resume/deny+resume)."""
    job = registry.create(kind, payload, execute_local=bool(payload.get("execute_local", True)))
    before_ids = _existing_run_ids(runs_root)

    def _worker() -> None:
        job.status = "running"
        try:
            return_code = step()
            job.status = "completed" if return_code == 0 else "failed"
            if return_code != 0:
                job.error = f"command exited with status {return_code}"
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)

    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(
        target=_watch_runs,
        args=(job, runs_root, before_ids, broker),
        kwargs={"seed_run_ids": {run_id}},
        daemon=True,
    ).start()
    return job


# --- HTTP handler -----------------------------------------------------------


def _is_safe_run_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def serve_ops(
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    """Start the web ops console and block until interrupted."""
    registry = JobRegistry()
    broker = OpsBroker()
    handler = _handler_for(registry, broker)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"miniCC ops console (experimental): http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOps console stopped.")
    finally:
        server.server_close()


def _handler_for(
    registry: JobRegistry,
    broker: OpsBroker,
) -> type[BaseHTTPRequestHandler]:
    from minicc import cli as minicc_cli

    runs_root = Path.cwd() / ".minicc" / "runs"

    class OpsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            segments = self._segments()
            try:
                if segments == []:
                    self._send_html(render_ops_index())
                    return
                if segments == ["api", "config"]:
                    self._send_json(minicc_cli._provider_summary(minicc_cli.load_settings()))
                    return
                if segments == ["api", "eval", "cases"]:
                    self._handle_eval_cases()
                    return
                if len(segments) == 2 and segments[0] == "api" and segments[1] in {"runs", "eval", "replay"}:
                    self._send_json([job_to_dict(j) for j in registry.list(segments[1])])
                    return
                if len(segments) == 3 and segments[0] == "api" and segments[1] in {"runs", "eval", "replay"}:
                    self._send_job_detail(segments[1], segments[2])
                    return
                if (
                    len(segments) == 4
                    and segments[0] == "api"
                    and segments[1] in {"runs", "eval", "replay"}
                    and segments[3] == "events"
                ):
                    self._handle_job_events(segments[1], segments[2])
                    return
            except Exception as exc:  # noqa: BLE001 - never crash the server on a bad request
                self._send_error(500, str(exc))
                return
            self._not_found()

        def do_POST(self) -> None:
            segments = self._segments()
            body = self._read_json_body()
            try:
                if segments == ["api", "models", "discover"]:
                    self._handle_models_discover(body)
                    return
                if segments == ["api", "runs"]:
                    self._handle_launch("runs", body, _run_namespace, minicc_cli.run_command)
                    return
                if segments == ["api", "eval"]:
                    self._handle_launch("eval", body, _eval_namespace, minicc_cli.eval_command)
                    return
                if segments == ["api", "replay", "create"]:
                    self._handle_replay_create(body)
                    return
                if segments == ["api", "replay", "run"]:
                    self._handle_launch("replay", body, _replay_run_namespace, minicc_cli.replay_run_command)
                    return
                if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] in {"approve", "deny"}:
                    self._handle_approve_deny(segments[2], segments[3], body)
                    return
            except Exception as exc:  # noqa: BLE001
                self._send_error(500, str(exc))
                return
            self._not_found()

        # -- route bodies -----------------------------------------------------

        def _handle_models_discover(self, body: dict[str, Any]) -> None:
            from minicc.core.discovery import discover_models
            from minicc.core.provider import ProviderError

            settings = minicc_cli.load_settings()
            route_name = str(body.get("route") or "") or settings.default_provider
            route = settings.providers.get(route_name)
            if route is None:
                self._send_error(404, f"Unknown provider route: {route_name!r}")
                return
            api_key = str(body.get("probe_key") or "") or route.api_key
            if not api_key:
                self._send_error(400, f"Route {route_name!r} has no API key; pass probe_key.")
                return
            try:
                models = discover_models(
                    route.base_url,
                    api_key,
                    headers=route.headers or None,
                    timeout_ms=route.timeout_ms,
                )
            except ProviderError as exc:
                self._send_error(502, f"{exc.failure.code}: {exc.failure.message}")
                return
            self._send_json(
                [
                    {
                        "id": model.id,
                        "context_window": model.context_window,
                        "max_output_tokens": model.max_output_tokens,
                    }
                    for model in models
                ]
            )

        def _handle_eval_cases(self) -> None:
            from minicc.evals.case import discover_cases

            query = parse_qs(urlsplit(self.path).query)
            path = str((query.get("path") or ["eval_cases"])[0])
            try:
                cases = discover_cases(Path(path))
            except OSError as exc:
                self._send_error(400, str(exc))
                return
            self._send_json([{"name": case.name, "sandbox_mode": case.sandbox_mode} for case in cases])

        def _handle_launch(
            self,
            kind: JobKind,
            body: dict[str, Any],
            build_namespace: Callable[[dict[str, Any]], argparse.Namespace],
            command: Callable[[argparse.Namespace], int],
        ) -> None:
            job = launch_job(registry, broker, kind, body, command, build_namespace, runs_root=runs_root)
            self._send_json(job_to_dict(job))

        def _handle_replay_create(self, body: dict[str, Any]) -> None:
            from minicc.trace.replay import (
                ReplayError,
                create_replay_case,
                create_replay_case_from_eval_case,
            )

            run_id = str(body.get("run_id") or "").strip()
            if not run_id:
                self._send_error(400, "run_id is required")
                return
            output_dir = Path(str(body["output_dir"])) if body.get("output_dir") else None
            overwrite = bool(body.get("overwrite", False))
            raw = Path(run_id)
            try:
                if raw.is_file() and raw.name in {"case.yaml", "case.yml"}:
                    case_dir = create_replay_case_from_eval_case(raw, output_dir=output_dir, overwrite=overwrite)
                elif raw.is_dir() and (raw / "case.yaml").is_file() and not (raw / "state.json").is_file():
                    case_dir = create_replay_case_from_eval_case(raw, output_dir=output_dir, overwrite=overwrite)
                else:
                    run_dir = raw if raw.is_dir() else runs_root / run_id
                    case_dir = create_replay_case(run_dir, output_dir=output_dir, overwrite=overwrite)
            except (ReplayError, OSError) as exc:
                self._send_error(400, str(exc))
                return
            manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
            self._send_json(
                {
                    "case_dir": str(case_dir),
                    "case_id": manifest.get("case_id", case_dir.name),
                    "deterministic_eligible": manifest.get("deterministic_eligible", False),
                    "fresh_eligible": manifest.get("fresh_eligible", False),
                }
            )

        def _handle_approve_deny(self, run_id: str, verb: str, body: dict[str, Any]) -> None:
            """Record the approve/deny decision, then auto-resume the run.

            ``approve_command``/``deny_command`` only record the decision and
            tell the CLI user to separately run ``resume``; the web console
            chains that resume step itself so one click both decides and
            continues the run.
            """
            if not _is_safe_run_id(run_id):
                self._send_error(404, "Not found")
                return
            session = minicc_cli.SessionManager()
            state = minicc_cli._load_waiting_state(session, run_id, require_pending_action=(verb == "approve"))
            if state is None:
                self._send_error(409, f"Run {run_id} is not waiting for approval with a pending action.")
                return
            if verb == "approve":
                session.approve(state)
            else:
                reason = str(body.get("reason") or "User denied the action.")
                session.deny(state, reason)

            resume_namespace = argparse.Namespace(
                run_id=run_id,
                execute_local=bool(body.get("execute_local", True)),
                from_checkpoint=False,
            )
            job = launch_resume_like_job(
                registry,
                broker,
                "runs",
                {"run_id": run_id, "verb": verb, **body},
                run_id,
                lambda: minicc_cli.resume_command(resume_namespace),
                runs_root=runs_root,
            )
            self._send_json(job_to_dict(job))

        def _send_job_detail(self, kind: JobKind, job_id: str) -> None:
            job = registry.get(job_id)
            if job is None or job.kind != kind:
                self._send_error(404, "Job not found")
                return
            payload = job_to_dict(job)
            if kind == "runs" and job.run_ids:
                payload["pending_approval"] = self._pending_approval(job.run_ids[-1])
            self._send_json(payload)

        def _pending_approval(self, run_id: str) -> dict[str, Any] | None:
            from minicc.core.protocol import action_to_dict

            try:
                state = minicc_cli.SessionManager().load(run_id)
            except (OSError, ValueError):
                return None
            if state.status != "waiting_approval" or state.pending_action is None:
                return None
            return {
                "run_id": run_id,
                "approval_question": state.approval_question,
                "pending_action": action_to_dict(state.pending_action),
            }

        def _handle_job_events(self, kind: JobKind, job_id: str) -> None:
            job = registry.get(job_id)
            if job is None or job.kind != kind:
                self._send_error(404, "Job not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            subscriber = broker.subscribe(job_id)
            try:
                self._sse_write({"type": "snapshot", "job": job_to_dict(job)})
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
                broker.unsubscribe(job_id, subscriber)

        # -- transport helpers -------------------------------------------------

        def _segments(self) -> list[str]:
            return [seg for seg in unquote(urlsplit(self.path).path).split("/") if seg]

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
            self.wfile.write(json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"))

        def _not_found(self) -> None:
            self._send_error(404, "Not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return OpsHandler


# --- frontend ----------------------------------------------------------------


def render_ops_index() -> str:
    """One-page ops console UI; all data flows through the JSON/SSE API above."""
    return _OPS_INDEX_HTML


_OPS_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>miniCC Ops Console</title>
  <style>
    :root { --bg:#f4f6f8; --panel:#fff; --ink:#16202a; --muted:#6b7785; --line:#dbe2e8;
      --accent:#0b7a75; --accent-soft:#e4f4f1; --accent-dark:#075e5b; --danger:#b42318;
      --warn-bg:#fff8e8; --shadow:0 1px 2px rgba(16,24,40,.05); }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--ink);
      font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    button, input, select, textarea { font: inherit; }
    button { border:1px solid var(--line); background:var(--panel); color:var(--ink); border-radius:6px;
      padding:8px 12px; cursor:pointer; transition:background .15s,border-color .15s,opacity .15s; }
    button:hover:not(:disabled) { border-color:var(--accent); background:#f7fbfb; }
    button:disabled { cursor:not-allowed; opacity:.55; }
    button.primary { background:var(--accent); color:#fff; border-color:var(--accent); font-weight:600; }
    button.primary:hover:not(:disabled) { background:var(--accent-dark); }
    button.danger { color: var(--danger); border-color: var(--danger); }
    input, select, textarea { border:1px solid var(--line); border-radius:6px; padding:9px 10px;
      width:100%; background:#fff; outline:none; }
    input:focus, select:focus, textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(11,122,117,.12); }
    label { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
    .app { display: grid; grid-template-rows: auto auto 1fr; min-height: 100vh; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px;
      padding:14px 20px; border-bottom:1px solid var(--line); background:var(--panel); box-shadow:var(--shadow); }
    h1 { margin: 0; font-size: 18px; }
    .subtle { color: var(--muted); font-size: 12px; }
    nav.tabs { display:flex; gap:4px; padding:8px 20px 0; background:var(--panel);
      border-bottom: 1px solid var(--line); }
    nav.tabs button { border-radius: 6px 6px 0 0; border-bottom-color: var(--panel); }
    nav.tabs button.active { background: var(--accent-soft); border-color: var(--accent);
      color: var(--accent); font-weight: 600; }
    main { padding:20px; overflow:auto; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .grid2 { display:grid; grid-template-columns:minmax(300px,380px) 1fr; gap:18px; align-items:start; max-width:1440px; margin:0 auto; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
      padding:16px; margin-bottom:16px; box-shadow:var(--shadow); }
    .card h2 { margin: 0 0 10px; font-size: 14px; }
    .field-grid { display: grid; gap: 10px; }
    .row { display: flex; gap: 8px; align-items: center; }
    .row.between { justify-content: space-between; }
    .checkbox-row { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink); }
    .checkbox-row input { width: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
    .job-list { display: grid; gap: 6px; max-height: 220px; overflow: auto; margin-bottom: 10px; }
    .job-item { width:100%; text-align:left; padding:10px 11px; display:grid; gap:2px; border-color:transparent; }
    .job-item.active { border-color: var(--accent); background: #f0fbf9; }
    .job-id { font-family: monospace; font-size: 12px; }
    .status-badge { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
      font-weight: 600; }
    .status-queued, .status-running { background: #e0ecff; color: #1d4ed8; }
    .status-completed { background: #dcf5e6; color: #087443; }
    .status-failed { background: #fde2e1; color: var(--danger); }
    .approval-bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
      padding:12px; border-radius:8px; background:var(--warn-bg); margin-bottom:12px; border:1px solid #efd9a6; }
    .approval-bar .grow { flex: 1; min-width: 200px; }
    .approval-command { font-family: monospace; color: var(--danger); overflow-wrap: anywhere; }
    .log { background: #0f172a; color: #d7e2ea; font-family: monospace; font-size: 12px;
      border-radius: 8px; padding: 10px; max-height: 360px; overflow: auto; white-space: pre-wrap; }
    .empty { color: var(--muted); padding: 10px 0; }
    .hidden { display: none !important; }
    textarea.mono { font-family:monospace; min-height:60px; }
    @media (max-width:900px) { main { padding:14px; } .grid2 { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div><h1>miniCC Ops Console</h1><span class="subtle">CLI 操作的 Web 化视图（experimental）</span></div>
      <div id="configBadge" class="subtle"></div>
    </header>
    <nav class="tabs">
      <button data-tab="run" class="active">Run</button>
      <button data-tab="eval">Eval</button>
      <button data-tab="replay">Replay</button>
      <button data-tab="models">Models</button>
    </nav>
    <main>
      <section id="tab-run" class="tab-panel active">
        <div class="grid2">
          <div>
            <div class="card">
              <h2>Launch run</h2>
              <div class="field-grid">
                <label>Goal<textarea id="runGoal" class="mono" placeholder="describe the task..."></textarea></label>
                <label>Milestone<input id="runMilestone" placeholder="(optional)"></label>
                <label>Profile
                  <select id="runProfile">
                    <option value="">(default)</option>
                    <option value="baseline-bash">baseline-bash</option>
                    <option value="hybrid-v3.6">hybrid-v3.6</option>
                    <option value="multi-agent-v4">multi-agent-v4</option>
                  </select>
                </label>
                <label>Verify command(s), one per line<textarea id="runVerify" class="mono"></textarea></label>
                <div class="checkbox-row"><input id="runExecuteLocal" type="checkbox" checked>
                  <label for="runExecuteLocal">execute locally (uncheck = Docker sandbox)</label></div>
                <button id="runLaunch" class="primary" type="button">Launch run</button>
              </div>
            </div>
            <div class="card">
              <h2>Run jobs</h2>
              <div id="runJobList" class="job-list"><div class="empty">No jobs yet.</div></div>
            </div>
          </div>
          <div class="card">
            <h2>Job detail</h2>
            <div id="runApprovalBar" class="approval-bar hidden">
              <div class="grow">
                <div id="runApprovalQuestion" class="subtle"></div>
                <div id="runApprovalCommand" class="approval-command"></div>
              </div>
              <button id="runApproveBtn" type="button">Approve</button>
              <button id="runDenyBtn" class="danger" type="button">Deny</button>
            </div>
            <div id="runDetail" class="subtle">Select a job.</div>
            <div id="runLog" class="log"></div>
          </div>
        </div>
      </section>

      <section id="tab-eval" class="tab-panel">
        <div class="grid2">
          <div>
            <div class="card">
              <h2>Launch eval</h2>
              <div class="field-grid">
                <label>Cases path<input id="evalPath" value="eval_cases"></label>
                <button id="evalListCases" type="button">List cases</button>
                <div id="evalCases" class="subtle"></div>
                <label>Case names (optional, comma-separated)<input id="evalCaseNames"></label>
                <label>Milestone<input id="evalMilestone" placeholder="(optional)"></label>
                <label>Repeat<input id="evalRepeat" type="number" min="1" value="1"></label>
                <label>Output dir<input id="evalOutputDir" placeholder="(optional)"></label>
                <div class="checkbox-row"><input id="evalExecuteLocal" type="checkbox" checked>
                  <label for="evalExecuteLocal">execute locally</label></div>
                <button id="evalLaunch" class="primary" type="button">Launch eval</button>
              </div>
            </div>
            <div class="card">
              <h2>Eval jobs</h2>
              <div id="evalJobList" class="job-list"><div class="empty">No jobs yet.</div></div>
            </div>
          </div>
          <div class="card">
            <h2>Job detail</h2>
            <div id="evalDetail" class="subtle">Select a job.</div>
            <div id="evalLog" class="log"></div>
          </div>
        </div>
      </section>

      <section id="tab-replay" class="tab-panel">
        <div class="grid2">
          <div>
            <div class="card">
              <h2>Create replay case</h2>
              <div class="field-grid">
                <label>run_id or case.yaml path<input id="replaySource" placeholder="run_id / path/to/case.yaml"></label>
                <label>Output dir<input id="replayCreateOutputDir" placeholder="(optional)"></label>
                <div class="checkbox-row"><input id="replayOverwrite" type="checkbox">
                  <label for="replayOverwrite">overwrite existing case</label></div>
                <button id="replayCreate" type="button">Create case</button>
                <div id="replayCreateResult" class="subtle"></div>
              </div>
            </div>
            <div class="card">
              <h2>Run replay</h2>
              <div class="field-grid">
                <label>Case dir<input id="replayCaseDir" placeholder="path to replay case dir"></label>
                <div class="checkbox-row"><input id="replayFresh" type="checkbox">
                  <label for="replayFresh">fresh replay (re-run model, not deterministic)</label></div>
                <div class="checkbox-row"><input id="replayExecuteLocal" type="checkbox" checked>
                  <label for="replayExecuteLocal">execute locally</label></div>
                <button id="replayLaunch" class="primary" type="button">Launch replay run</button>
              </div>
            </div>
            <div class="card">
              <h2>Replay jobs</h2>
              <div id="replayJobList" class="job-list"><div class="empty">No jobs yet.</div></div>
            </div>
          </div>
          <div class="card">
            <h2>Job detail</h2>
            <div id="replayDetail" class="subtle">Select a job.</div>
            <div id="replayLog" class="log"></div>
          </div>
        </div>
      </section>

      <section id="tab-models" class="tab-panel">
        <div class="card">
          <h2>Provider config</h2>
          <div id="modelsConfig" class="subtle">Loading...</div>
        </div>
        <div class="card">
          <h2>Discover models</h2>
          <div class="field-grid">
            <label>Route (blank = default)<input id="modelsRoute"></label>
            <label>Probe key (optional, overrides route's key)<input id="modelsProbeKey" type="password"></label>
            <button id="modelsDiscover" class="primary" type="button">Discover</button>
          </div>
          <table id="modelsTable" class="hidden">
            <thead><tr><th>id</th><th>context_window</th><th>max_output_tokens</th></tr></thead>
            <tbody></tbody>
          </table>
          <div id="modelsResult" class="subtle"></div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    function escapeHtml(v) {
      return String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    async function fetchJson(url, opts) {
      const res = await fetch(url, opts);
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (await res.json()).error || msg; } catch (e) {}
        throw new Error(msg);
      }
      return res.json();
    }

    // -- tabs -----------------------------------------------------------------
    document.querySelectorAll('nav.tabs button').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('nav.tabs button').forEach((b) => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        $('tab-' + btn.dataset.tab).classList.add('active');
      });
    });

    // -- generic job tracker (run / eval / replay share this shape) -----------
    function makeJobPanel(kind, listApiPath, els) {
      const panel = { jobs: [], activeId: null, events: null };

      async function refreshList() {
        panel.jobs = await fetchJson(listApiPath);
        renderList();
      }
      function renderList() {
        els.list.innerHTML = panel.jobs.length ? panel.jobs.map((j) => `
          <button class="job-item ${j.job_id === panel.activeId ? 'active' : ''}" data-id="${escapeHtml(j.job_id)}">
            <span class="job-id">${escapeHtml(j.job_id)}</span>
            <span><span class="status-badge status-${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
              ${j.run_ids.length ? escapeHtml(j.run_ids[j.run_ids.length - 1]) : ''}</span>
          </button>`).join('') : '<div class="empty">No jobs yet.</div>';
        els.list.querySelectorAll('[data-id]').forEach((b) => b.addEventListener('click', () => selectJob(b.dataset.id)));
      }
      async function selectJob(jobId) {
        panel.activeId = jobId;
        renderList();
        els.log.textContent = '';
        if (els.approvalBar) els.approvalBar.classList.add('hidden');
        const detail = await fetchJson(listApiPath + '/' + encodeURIComponent(jobId));
        renderDetail(detail);
        subscribe(jobId);
      }
      function renderDetail(detail) {
        els.detail.innerHTML = `<div><span class="status-badge status-${escapeHtml(detail.status)}">${escapeHtml(detail.status)}</span>
          ${detail.error ? ' <span style="color:var(--danger)">' + escapeHtml(detail.error) + '</span>' : ''}</div>
          <div class="subtle">run_ids: ${detail.run_ids.map(escapeHtml).join(', ') || '(none yet)'}</div>`;
        if (els.approvalBar) renderApproval(detail.pending_approval || null);
      }
      function renderApproval(pending) {
        if (!pending) { els.approvalBar.classList.add('hidden'); return; }
        els.approvalBar.classList.remove('hidden');
        els.approvalBar.dataset.runId = pending.run_id;
        els.approvalQuestion.textContent = pending.approval_question || 'Approve this action?';
        els.approvalCommand.textContent = (pending.pending_action && pending.pending_action.command) || '(no command)';
      }
      function subscribe(jobId) {
        if (panel.events) panel.events.close();
        panel.events = new EventSource(listApiPath + '/' + encodeURIComponent(jobId) + '/events');
        panel.events.onmessage = (e) => {
          const ev = JSON.parse(e.data);
          if (ev.type === 'snapshot') { renderDetail(ev.job); return; }
          if (ev.type === 'run_detected') { els.log.textContent += '[run] ' + ev.run_id + '\\n'; return; }
          if (ev.type === 'trace_event') {
            const summary = ev.event.type || ev.event.kind || JSON.stringify(ev.event).slice(0, 120);
            els.log.textContent += '[' + ev.run_id.slice(0, 8) + '] ' + summary + '\\n';
            els.log.scrollTop = els.log.scrollHeight;
            if (els.approvalBar && summary === 'approval_requested') refreshDetailSoon(jobId);
            return;
          }
          if (ev.type === 'job_status') {
            refreshList();
            fetchJson(listApiPath + '/' + encodeURIComponent(jobId)).then(renderDetail);
          }
        };
      }
      let refreshTimer = null;
      function refreshDetailSoon(jobId) {
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
          fetchJson(listApiPath + '/' + encodeURIComponent(jobId)).then(renderDetail).catch(() => {});
        }, 300);
      }
      refreshList().catch((e) => { els.list.innerHTML = '<div class="empty">' + escapeHtml(e.message) + '</div>'; });
      setInterval(() => refreshList().catch(() => {}), 5000);
      return { refreshList, get activeId() { return panel.activeId; } };
    }

    const runPanel = makeJobPanel('run', '/api/runs', {
      list: $('runJobList'), detail: $('runDetail'), log: $('runLog'),
      approvalBar: $('runApprovalBar'), approvalQuestion: $('runApprovalQuestion'), approvalCommand: $('runApprovalCommand'),
    });
    const evalPanel = makeJobPanel('eval', '/api/eval', { list: $('evalJobList'), detail: $('evalDetail'), log: $('evalLog') });
    const replayPanel = makeJobPanel('replay', '/api/replay', { list: $('replayJobList'), detail: $('replayDetail'), log: $('replayLog') });

    // -- run launcher -----------------------------------------------------------
    $('runLaunch').addEventListener('click', async () => {
      const goal = $('runGoal').value.trim();
      if (!goal) { alert('Goal is required.'); return; }
      const verify = $('runVerify').value.split('\\n').map((s) => s.trim()).filter(Boolean);
      try {
        await fetchJson('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          goal, milestone: $('runMilestone').value.trim() || null,
          profile: $('runProfile').value || null,
          verify_command: verify,
          execute_local: $('runExecuteLocal').checked,
        }) });
        await runPanel.refreshList();
      } catch (e) { alert(e.message); }
    });
    $('runApproveBtn').addEventListener('click', async () => {
      const runId = $('runApprovalBar').dataset.runId;
      if (!runId) return;
      $('runApprovalBar').classList.add('hidden');
      try {
        await fetchJson('/api/runs/' + encodeURIComponent(runId) + '/approve',
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        await runPanel.refreshList();
      } catch (e) { alert(e.message); }
    });
    $('runDenyBtn').addEventListener('click', async () => {
      const runId = $('runApprovalBar').dataset.runId;
      if (!runId) return;
      const reason = window.prompt('Reason for denial:', 'User denied the action.');
      if (reason === null) return;
      $('runApprovalBar').classList.add('hidden');
      try {
        await fetchJson('/api/runs/' + encodeURIComponent(runId) + '/deny',
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }) });
        await runPanel.refreshList();
      } catch (e) { alert(e.message); }
    });

    // -- eval launcher ------------------------------------------------------
    $('evalListCases').addEventListener('click', async () => {
      try {
        const cases = await fetchJson('/api/eval/cases?path=' + encodeURIComponent($('evalPath').value.trim() || 'eval_cases'));
        $('evalCases').textContent = cases.length ? cases.map((c) => c.name).join(', ') : '(no cases found)';
      } catch (e) { $('evalCases').textContent = e.message; }
    });
    $('evalLaunch').addEventListener('click', async () => {
      const path = $('evalPath').value.trim() || 'eval_cases';
      const caseNames = $('evalCaseNames').value.split(',').map((s) => s.trim()).filter(Boolean);
      try {
        await fetchJson('/api/eval', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          path, milestone: $('evalMilestone').value.trim() || null,
          repeat: parseInt($('evalRepeat').value, 10) || 1,
          output_dir: $('evalOutputDir').value.trim() || null,
          case_names: caseNames.length ? caseNames : null,
          execute_local: $('evalExecuteLocal').checked,
        }) });
        await evalPanel.refreshList();
      } catch (e) { alert(e.message); }
    });

    // -- replay -------------------------------------------------------------
    $('replayCreate').addEventListener('click', async () => {
      const source = $('replaySource').value.trim();
      if (!source) { alert('run_id or case.yaml path is required.'); return; }
      try {
        const result = await fetchJson('/api/replay/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          run_id: source,
          output_dir: $('replayCreateOutputDir').value.trim() || null,
          overwrite: $('replayOverwrite').checked,
        }) });
        $('replayCreateResult').textContent = 'Created: ' + result.case_dir
          + ' (deterministic_eligible=' + result.deterministic_eligible + ', fresh_eligible=' + result.fresh_eligible + ')';
        $('replayCaseDir').value = result.case_dir;
      } catch (e) { $('replayCreateResult').textContent = e.message; }
    });
    $('replayLaunch').addEventListener('click', async () => {
      const caseDir = $('replayCaseDir').value.trim();
      if (!caseDir) { alert('Case dir is required.'); return; }
      try {
        await fetchJson('/api/replay/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          case: caseDir, fresh: $('replayFresh').checked, execute_local: $('replayExecuteLocal').checked,
        }) });
        await replayPanel.refreshList();
      } catch (e) { alert(e.message); }
    });

    // -- models / config ------------------------------------------------------
    async function loadConfig() {
      const cfg = await fetchJson('/api/config');
      $('configBadge').textContent = cfg.model ? (cfg.base_url + ' · ' + cfg.model) : '';
      $('modelsConfig').innerHTML = `<pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(cfg, null, 2))}</pre>`;
    }
    $('modelsDiscover').addEventListener('click', async () => {
      $('modelsResult').textContent = 'Discovering...';
      try {
        const models = await fetchJson('/api/models/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          route: $('modelsRoute').value.trim() || null,
          probe_key: $('modelsProbeKey').value.trim() || null,
        }) });
        $('modelsResult').textContent = models.length + ' model(s) found.';
        const tbody = $('modelsTable').querySelector('tbody');
        tbody.innerHTML = models.map((m) => `<tr><td>${escapeHtml(m.id)}</td><td>${escapeHtml(m.context_window)}</td><td>${escapeHtml(m.max_output_tokens)}</td></tr>`).join('');
        $('modelsTable').classList.toggle('hidden', models.length === 0);
      } catch (e) { $('modelsResult').textContent = e.message; }
    });

    loadConfig().catch((e) => { $('modelsConfig').textContent = e.message; });
  </script>
</body>
</html>
"""
