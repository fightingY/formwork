"""Deterministic V4 multi-agent orchestration primitives.

This module provides protocol-level child lifecycle and scheduling contracts. It
does not replace the existing AgentLoop; callers may supply an in-process
driver, while the subprocess driver uses ``minicc childrun`` JSONL.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from minicc.config import load_settings
from minicc.core.protocol import DelegateAction, DelegateTask
from minicc.core.provider import CompletionOptions, OpenAICompatibleProvider, ProviderError
from minicc.runtime import ChildCapabilities, WorkspaceWriteLease

PROTOCOL_VERSION = 1
MAX_DELEGATE_DEPTH = 4


@dataclass(frozen=True)
class ChildResult:
    task_id: str
    child_run_id: str
    status: str
    role: str
    summary: str = ""
    findings: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[str, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"findings": list(self.findings), "artifacts": list(self.artifacts)}


@dataclass(frozen=True)
class ChildEvent:
    type: str
    event: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class ChildRunProvider(Protocol):
    def run(self, task: DelegateTask, *, parent_run_id: str, root_run_id: str, workflow_id: str, cancel: threading.Event | None = None) -> ChildResult:
        ...


class InProcessChildRunProvider:
    def __init__(self, handler: Callable[..., ChildResult | dict[str, Any]] | None = None) -> None:
        self.handler = handler or self._default

    def run(self, task: DelegateTask, *, parent_run_id: str, root_run_id: str, workflow_id: str, cancel: threading.Event | None = None) -> ChildResult:
        child_id = f"child-{uuid4().hex[:12]}"
        if cancel is not None and cancel.is_set():
            return ChildResult(task.id, child_id, "cancelled", task.role, failure={"code": "CANCELLED"})
        try:
            raw = self.handler(task=task, parent_run_id=parent_run_id, root_run_id=root_run_id, workflow_id=workflow_id)
            if isinstance(raw, ChildResult):
                return raw
            return ChildResult(
                task.id, child_id, str(raw.get("status", "completed")), task.role,
                str(raw.get("summary", "")), tuple(raw.get("findings", ())), tuple(raw.get("artifacts", ())),
                dict(raw.get("usage", {})), raw.get("failure"),
            )
        except Exception as exc:  # child failures are normalized, never leaked into parent state
            return ChildResult(task.id, child_id, "failed", task.role, failure={"code": "CHILD_ERROR", "message": str(exc)})

    @staticmethod
    def _default(*, task: DelegateTask, **_: Any) -> dict[str, Any]:
        return {"status": "completed", "summary": f"Child {task.role} completed: {task.goal}"}


class SubprocessChildRunProvider:
    """Run miniCC's own childrun command over JSONL stdout."""

    def __init__(self, *, python: str | None = None, timeout_sec: float | None = None, use_model: bool = True) -> None:
        self.python = python or sys.executable
        self.timeout_sec = timeout_sec
        self.use_model = use_model

    def run(self, task: DelegateTask, *, parent_run_id: str, root_run_id: str, workflow_id: str, cancel: threading.Event | None = None) -> ChildResult:
        child_run_id = f"child-{uuid4().hex[:12]}"
        request = {
            "type": "child_start", "protocol_version": PROTOCOL_VERSION,
            "root_run_id": root_run_id, "parent_run_id": parent_run_id,
            "child_run_id": child_run_id, "workflow_id": workflow_id, "task": asdict(task),
            "execute_model": self.use_model,
        }
        proc = subprocess.Popen(
            [self.python, "-m", "minicc.cli", "childrun"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.close()
            lines: list[dict[str, Any]] = []
            deadline = time.monotonic() + (self.timeout_sec or task.timeout_sec)
            while proc.stdout is not None:
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    return ChildResult(task.id, child_run_id, "cancelled", task.role, failure={"code": "CANCELLED"})
                if time.monotonic() > deadline:
                    proc.kill()
                    return ChildResult(task.id, child_run_id, "timeout", task.role, failure={"code": "TIMEOUT"})
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return ChildResult(task.id, child_run_id, "failed", task.role, failure={"code": "INVALID_EVENT"})
                if isinstance(event, dict):
                    lines.append(event)
            code = proc.wait(timeout=2)
            result_event = next((e for e in reversed(lines) if e.get("type") == "child_result"), None)
            if result_event is None:
                return ChildResult(task.id, child_run_id, "failed", task.role, failure={"code": "CHILD_PROCESS_ERROR", "exit_code": code})
            raw_result = result_event.get("result")
            result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else dict(result_event)
            return ChildResult(
                task.id, child_run_id, str(result.get("status", "failed")), task.role,
                str(result.get("summary", "")), tuple(result.get("findings", ())), tuple(result.get("artifacts", ())),
                dict(result.get("usage", {})), result.get("failure"),
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            return ChildResult(task.id, child_run_id, "timeout", task.role, failure={"code": "TIMEOUT"})
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


@dataclass
class WorkflowResult:
    workflow_id: str
    status: str
    results: list[ChildResult]
    max_concurrent: int = 0
    failure: dict[str, Any] | None = None

    def summary_observation(self) -> dict[str, Any]:
        return {
            "kind": "workflow_summary_observation", "workflow_id": self.workflow_id,
            "status": self.status, "results": [result.to_dict() for result in self.results],
        }


class WorkflowCoordinator:
    def __init__(self, provider: ChildRunProvider, *, max_concurrent_children: int = 4, lease: WorkspaceWriteLease | None = None) -> None:
        if max_concurrent_children <= 0:
            raise ValueError("max_concurrent_children must be positive")
        self.provider = provider
        self.max_concurrent_children = max_concurrent_children
        self.lease = lease or WorkspaceWriteLease()

    def execute(self, action: DelegateAction, *, parent_run_id: str, root_run_id: str | None = None, workflow_id: str | None = None, cancel: threading.Event | None = None) -> WorkflowResult:
        validate_delegate(action)
        workflow_id = workflow_id or f"wf-{uuid4().hex[:12]}"
        root_run_id = root_run_id or parent_run_id
        tasks = {task.id: task for task in action.tasks}
        results: dict[str, ChildResult] = {}
        started: set[str] = set()
        max_seen = 0
        while len(results) < len(tasks):
            if cancel is not None and cancel.is_set():
                for task_id in tasks:
                    if task_id not in results:
                        results[task_id] = ChildResult(task_id, f"child-{uuid4().hex[:12]}", "aborted_before_dispatch", tasks[task_id].role, failure={"code": "CANCELLED"})
                break
            ready = [task for task in tasks.values() if task.id not in started and all(dep in results and results[dep].status == "completed" for dep in task.depends_on)]
            if not ready:
                if len(results) < len(tasks):
                    return WorkflowResult(workflow_id, "failed", list(results.values()), max_seen, {"code": "DEPENDENCY_BLOCKED"})
                break
            read_ready = [task for task in ready if task.role != "worker"]
            worker_ready = [task for task in ready if task.role == "worker"]
            batch = read_ready[: self.max_concurrent_children]
            if not batch and worker_ready:
                batch = worker_ready[:1]
            for task in batch:
                started.add(task.id)
            max_seen = max(max_seen, len(batch))
            with ThreadPoolExecutor(max_workers=min(self.max_concurrent_children, len(batch))) as pool:
                futures = {
                    pool.submit(self._run_task, task, parent_run_id, root_run_id, workflow_id, cancel): task.id
                    for task in batch
                }
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
            if action.join == "any" and any(result.status == "completed" for result in results.values()):
                for task in tasks.values():
                    if task.id not in results:
                        results[task.id] = ChildResult(task.id, f"child-{uuid4().hex[:12]}", "cancelled", task.role, failure={"code": "JOIN_ANY"})
                break
        ordered = [results[task.id] for task in action.tasks]
        status = "completed" if all(result.status == "completed" for result in ordered) else "failed"
        return WorkflowResult(workflow_id, status, ordered, max_seen)

    def _run_task(self, task: DelegateTask, parent_run_id: str, root_run_id: str, workflow_id: str, cancel: threading.Event | None) -> ChildResult:
        if task.role == "worker":
            epoch = self.lease.acquire(parent_run_id, task.id)
            if epoch is None:
                return ChildResult(task.id, f"child-{uuid4().hex[:12]}", "failed", task.role, failure={"code": "WRITE_LEASE_DENIED"})
            try:
                return self.provider.run(task, parent_run_id=parent_run_id, root_run_id=root_run_id, workflow_id=workflow_id, cancel=cancel)
            finally:
                self.lease.release(parent_run_id, task.id, epoch)
        return self.provider.run(task, parent_run_id=parent_run_id, root_run_id=root_run_id, workflow_id=workflow_id, cancel=cancel)


def standard_scout_planner_worker(*, scout_goals: Iterable[str], planner_goal: str, worker_goal: str) -> DelegateAction:
    scouts = tuple(DelegateTask(f"scout-{index + 1}", "scout", goal, "scout") for index, goal in enumerate(scout_goals))
    scout_ids = tuple(task.id for task in scouts)
    return DelegateAction(scouts + (
        DelegateTask("planner", "planner", planner_goal, "planner", scout_ids, output_schema="plan"),
        DelegateTask("worker", "worker", worker_goal, "worker", ("planner",), output_schema="worker_result"),
    ))


def reviewer_loop(*, worker_goal: str, reviewer_goal: str, max_iterations: int = 2) -> list[DelegateAction]:
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    actions: list[DelegateAction] = []
    previous = ""
    for index in range(max_iterations):
        worker_id = f"worker-{index + 1}"
        reviewer_id = f"reviewer-{index + 1}"
        actions.append(DelegateAction((
            DelegateTask(worker_id, "worker", worker_goal if not previous else f"{worker_goal}\nFindings: {previous}", "worker"),
            DelegateTask(reviewer_id, "reviewer", reviewer_goal, "reviewer", (worker_id,), output_schema="review_report"),
        )))
        previous = reviewer_id
    return actions


def validate_delegate(action: DelegateAction, *, parent_profile: str = "root", depth: int = 0, max_depth: int = MAX_DELEGATE_DEPTH) -> None:
    """Validate runtime constraints that require parent context."""
    if depth >= max_depth:
        raise ValueError("delegate exceeds the maximum workflow depth")
    parent = ChildCapabilities.for_profile(parent_profile)
    for task in action.tasks:
        child = ChildCapabilities.for_profile(task.capability_profile)
        if not child.tools.issubset(parent.tools):
            raise ValueError(f"delegate task {task.id!r} requests capability above parent profile")
        if task.role in {"scout", "planner", "reviewer"} and not child.read_only:
            raise ValueError(f"read-only role {task.role!r} must use a read-only capability profile")
        if task.role == "worker" and task.capability_profile != "worker":
            raise ValueError("worker tasks must use the worker capability profile")


def childrun_main(stdin: Any, stdout: Any) -> int:
    """Serve the minimal JSONL childrun transport used by the subprocess backend."""
    started = False
    finished = False
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"type": "child_result", "status": "failed", "failure": {"code": "INVALID_REQUEST"}}), file=stdout, flush=True)
            return 2
        if payload.get("type") != "child_start" or started:
            print(json.dumps({"type": "child_result", "status": "failed", "failure": {"code": "PROTOCOL_ERROR"}}), file=stdout, flush=True)
            return 2
        started = True
        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        child_run_id = str(payload.get("child_run_id") or f"child-{uuid4().hex[:12]}")
        print(json.dumps({"type": "child_start", "protocol_version": PROTOCOL_VERSION, "child_run_id": child_run_id}, ensure_ascii=False), file=stdout, flush=True)
        print(json.dumps({"type": "child_event", "event": "turn_started", "turn_id": "child-t1"}), file=stdout, flush=True)
        if payload.get("execute_model", True):
            result = _complete_child_model(task, child_run_id)
        else:
            result = {
                "task_id": str(task.get("id", "child")), "child_run_id": child_run_id,
                "status": "completed", "role": str(task.get("role", "scout")),
                "summary": f"Child completed: {task.get('goal', '')}", "findings": [], "artifacts": [],
                "usage": {"turns": 1}, "failure": None,
            }
        print(json.dumps({"type": "child_result", "status": "completed", "result": result}, ensure_ascii=False), file=stdout, flush=True)
        finished = True
        break
    return 0 if started and finished else 2


def _complete_child_model(task: dict[str, Any], child_run_id: str) -> dict[str, Any]:
    """Execute one real child model turn using the configured child provider."""
    settings = load_settings()
    child = settings.child_provider or settings.provider
    if not child.base_url or not child.api_key or not child.model:
        return {
            "task_id": str(task.get("id", "child")), "child_run_id": child_run_id,
            "status": "failed", "role": str(task.get("role", "scout")),
            "summary": "", "findings": [], "artifacts": [], "usage": {},
            "failure": {"code": "CHILD_PROVIDER_CONFIG_MISSING"},
        }
    provider = OpenAICompatibleProvider(
        base_url=child.base_url, api_key=child.api_key, model=child.model,
        timeout_sec=child.timeout_sec, max_retries=child.max_retries,
    )
    role = str(task.get("role", "scout"))
    goal = str(task.get("goal", ""))
    system = (
        "You are a child coding agent in miniCC. Return one concise JSON object with "
        "summary, findings, and evidence. Do not claim hidden reasoning. "
        f"Your role is {role}; obey its capabilities and do not edit files."
    )
    try:
        response = provider.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": goal}],
            options=CompletionOptions(
                temperature=child.temperature, stream=child.stream,
                include_usage=child.include_usage, json_mode=child.json_mode,
                max_tokens=child.max_completion_tokens,
            ),
        )
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            parsed = {"summary": response.text[:4000], "findings": []}
        if not isinstance(parsed, dict):
            parsed = {"summary": str(parsed), "findings": []}
        return {
            "task_id": str(task.get("id", "child")), "child_run_id": child_run_id,
            "status": "completed", "role": role,
            "summary": str(parsed.get("summary", "")),
            "findings": parsed.get("findings", []) if isinstance(parsed.get("findings", []), list) else [],
            "artifacts": parsed.get("artifacts", []) if isinstance(parsed.get("artifacts", []), list) else [],
            "usage": {"prompt_tokens": response.usage.prompt_tokens, "completion_tokens": response.usage.completion_tokens, "turns": 1},
            "failure": None,
        }
    except ProviderError as exc:
        return {
            "task_id": str(task.get("id", "child")), "child_run_id": child_run_id,
            "status": "failed", "role": role, "summary": "", "findings": [], "artifacts": [], "usage": {},
            "failure": {"code": "CHILD_PROVIDER_ERROR", "message": str(exc)},
        }
    finally:
        provider.close()
