"""In-process multi-agent orchestration.

The manager deliberately sits beside :class:`AgentLoop`.  A child is a normal
loop with a new ``RunState`` and a private trajectory; the parent only receives
the settled ``ChildResult`` and its bounded facts.  ``fork`` snapshots the
parent's completed context at admission time, so later parent mutations cannot
change a running child.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from minicc.core.events import EventLog
from minicc.core.protocol import action_to_dict
from minicc.core.state import RunState, TrajectoryStep

ChildStatus = Literal["completed", "failed", "interrupted", "cancelled", "timeout"]
JoinMode = Literal["all", "any"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class Evidence:
    path: str
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class Fact:
    """A parent-consumable claim.  Raw child reasoning is intentionally absent."""

    claim: str
    evidence: tuple[Evidence, ...] = ()
    confidence: Literal["low", "medium", "high"] = "medium"


@dataclass(frozen=True)
class ChildTask:
    task_id: str
    goal: str
    role: str = "worker"
    capability_profile: str = "worker"
    provider: Literal["spawn", "fork"] = "spawn"
    depends_on: tuple[str, ...] = ()
    max_turns: int = 0
    timeout_sec: float = 0
    output_schema: str | None = None


@dataclass
class ChildResult:
    task_id: str
    child_run_id: str
    status: ChildStatus
    role: str
    summary: str = ""
    facts: list[Fact] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["facts"] = [asdict(fact) for fact in self.facts]
        return value


@dataclass(frozen=True)
class ForkSnapshot:
    """Immutable completed-turn prefix captured when a child is admitted."""

    session_history: tuple[dict[str, str], ...]
    trajectory: tuple[TrajectoryStep, ...]


class MultiAgentError(ValueError):
    pass


@dataclass
class JobRecord:
    """Durable-facing handle for a background one-shot workflow."""

    job_id: str
    workflow_id: str
    status: JobStatus = "queued"
    result: list[ChildResult] | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "result": [item.to_dict() for item in self.result] if self.result is not None else None,
            "error": self.error,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
        }


class JobRegistry:
    """In-process registry for non-blocking child workflows.

    A wait timeout only stops the caller waiting; it never kills the job.  This
    mirrors DSH Jobs semantics and leaves cancellation explicit via ``cancel``.
    """

    def __init__(self, *, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        fn: Callable[[threading.Event], list[ChildResult]],
        *,
        workflow_id: str,
        job_id: str | None = None,
    ) -> JobRecord:
        record = JobRecord(job_id=job_id or f"job-{uuid4().hex[:12]}", workflow_id=workflow_id)
        with self._lock:
            self._jobs[record.job_id] = record

        def worker() -> None:
            with self._lock:
                record.status = "running"
            try:
                if record._cancel.is_set():
                    record.status = "cancelled"
                    record.result = []
                else:
                    record.result = fn(record._cancel)
                    record.status = "cancelled" if record._cancel.is_set() else "completed"
            except Exception as exc:  # background failures are data on the job
                record.error = f"{type(exc).__name__}: {exc}"
                record.status = "failed"
            finally:
                record.completed_at = time.monotonic()
                record._done.set()

        self._pool.submit(worker)
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout_sec: float | None = None) -> JobRecord | None:
        record = self.get(job_id)
        if record is None:
            return None
        record._done.wait(timeout=None if timeout_sec is None else max(0.0, timeout_sec))
        return record if record._done.is_set() else None

    def cancel(self, job_id: str) -> bool:
        record = self.get(job_id)
        if record is None or record._done.is_set():
            return False
        record._cancel.set()
        return True

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class WorkspaceLeaseRegistry:
    """Process-wide write admission control for workers sharing a workspace."""

    _guard = threading.RLock()
    _locks: dict[str, threading.RLock] = {}
    _epochs: dict[str, int] = {}

    @classmethod
    def acquire(cls, workspace: Any) -> tuple[threading.RLock, int]:
        key = str(workspace.resolve()) if workspace is not None else "<none>"
        with cls._guard:
            lock = cls._locks.setdefault(key, threading.RLock())
            lock.acquire()
            epoch = cls._epochs.get(key, 0) + 1
            cls._epochs[key] = epoch
            return lock, epoch


FactExtractor = Callable[[RunState, list[TrajectoryStep]], list[Fact]]
LoopFactory = Callable[[RunState], Any]


class MultiAgentManager:
    """Run one-shot children with DSH-style fork isolation and settlement."""

    def __init__(
        self,
        loop_factory: LoopFactory,
        *,
        max_depth: int = 3,
        max_concurrent_children: int = 4,
        fact_extractor: FactExtractor | None = None,
        trace: Any | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        if max_depth < 0 or max_concurrent_children <= 0:
            raise ValueError("max_depth must be >= 0 and max_concurrent_children must be positive")
        self.loop_factory = loop_factory
        self.max_depth = max_depth
        self.max_concurrent_children = max_concurrent_children
        self.fact_extractor = fact_extractor or self._default_facts
        self.trace = trace
        self.jobs = jobs or JobRegistry(max_workers=max_concurrent_children)

    def run_background(
        self,
        parent: RunState,
        tasks: list[ChildTask] | tuple[ChildTask, ...],
        *,
        parent_trajectory: list[TrajectoryStep] | None = None,
        join: JoinMode = "all",
        workflow_id: str | None = None,
    ) -> JobRecord:
        """Submit a one-shot workflow and return immediately with a job handle."""
        workflow_id = workflow_id or f"wf-{uuid4().hex[:12]}"
        job_id = f"job-{uuid4().hex[:12]}"
        def execute(cancel: threading.Event) -> list[ChildResult]:
            self._event(parent, "job/update", {"job_id": job_id, "workflow_id": workflow_id, "status": "running"})
            try:
                result = self.run(
                    parent,
                    tasks,
                    parent_trajectory=parent_trajectory,
                    join=join,
                    workflow_id=workflow_id,
                    cancel_event=cancel,
                )
                final_status = "cancelled" if cancel.is_set() else "completed"
                self._event(parent, "job/update", {"job_id": job_id, "workflow_id": workflow_id, "status": final_status})
                self._event(parent, "job/end", {"job_id": job_id, "workflow_id": workflow_id, "status": final_status})
                return result
            except Exception as exc:
                self._event(parent, "job/end", {"job_id": job_id, "workflow_id": workflow_id, "status": "failed", "error": str(exc)})
                raise

        # ``execute`` references the handle for the completion event; submit
        # first with a tiny indirection so the closure is resolved at runtime.
        job = self.jobs.submit(
            execute,
            workflow_id=workflow_id,
            job_id=job_id,
        )
        self._event(parent, "job/start", {"job_id": job.job_id, "workflow_id": workflow_id})
        return job

    def snapshot(self, parent: RunState, trajectory: list[TrajectoryStep]) -> ForkSnapshot:
        """Copy only the completed prefix; in-flight/open steps are excluded."""
        copied = tuple(copy.deepcopy(trajectory))
        return ForkSnapshot(tuple(copy.deepcopy(parent.session_history)), copied)

    def run(
        self,
        parent: RunState,
        tasks: list[ChildTask] | tuple[ChildTask, ...],
        *,
        parent_trajectory: list[TrajectoryStep] | None = None,
        join: JoinMode = "all",
        workflow_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[ChildResult]:
        tasks = list(tasks)
        self._validate(parent, tasks, join)
        workflow_id = workflow_id or f"wf-{uuid4().hex[:12]}"
        snapshot = self.snapshot(parent, parent_trajectory or [])
        results: dict[str, ChildResult] = {}
        pending = {task.task_id: task for task in tasks}
        while pending:
            ready = [task for task in tasks if task.task_id in pending and all(dep in results for dep in task.depends_on)]
            if not ready:
                raise MultiAgentError("dependency graph cannot make progress")
            runnable: list[ChildTask] = []
            for task in ready:
                failed_deps = [results[dep] for dep in task.depends_on if results[dep].status != "completed"]
                if failed_deps:
                    results[task.task_id] = ChildResult(
                        task.task_id, "", "cancelled", task.role,
                        summary="Skipped because a dependency did not complete.",
                        failure="dependency_failed",
                    )
                    pending.pop(task.task_id)
                else:
                    runnable.append(task)
            if runnable:
                with ThreadPoolExecutor(max_workers=min(self.max_concurrent_children, len(runnable))) as pool:
                    futures = {
                        task.task_id: pool.submit(
                            self._run_child,
                            parent,
                            task,
                            snapshot,
                            workflow_id,
                            cancel_event,
                            [results[dep] for dep in task.depends_on],
                        )
                        for task in runnable
                    }
                    for task in runnable:
                        results[task.task_id] = self._resolve_future(futures[task.task_id], task)
                        pending.pop(task.task_id)
                if join == "any" and any(results[t.task_id].status == "completed" for t in runnable):
                    for task in list(pending.values()):
                        results[task.task_id] = ChildResult(task.task_id, "", "cancelled", task.role, failure="join_any_satisfied")
                        pending.pop(task.task_id)
        ordered = [results[task.task_id] for task in tasks]
        self._settle_parent(parent, ordered, workflow_id)
        return ordered

    def _run_child(
        self,
        parent: RunState,
        task: ChildTask,
        snapshot: ForkSnapshot,
        workflow_id: str,
        cancel_event: threading.Event | None,
        dependency_results: list[ChildResult],
    ) -> ChildResult:
        child = RunState.start(task.goal, workspace_host_path=parent.workspace_host_path)
        child.root_run_id = parent.root_run_id or parent.run_id
        child.parent_run_id = parent.run_id
        child.workflow_id = workflow_id
        child.task_id = task.task_id
        child.role = task.role
        child.capability_profile = task.capability_profile
        child.depth = parent.depth + 1
        parent_run_root = parent.run_dir or (Path.cwd() / ".minicc" / "runs" / parent.run_id)
        child.run_dir = parent_run_root / "children" / child.run_id
        child.run_dir.mkdir(parents=True, exist_ok=True)
        child._event_log = EventLog(child.run_dir / "events.jsonl", session_id=child.run_id)  # type: ignore[attr-defined]
        child._event_log.append(
            "session/start",
            {
                "session_id": child.run_id,
                "parent_run_id": parent.run_id,
                "workflow_id": workflow_id,
                "task_id": task.task_id,
            },
        )
        child._event_log.append(
            "subagent/descriptor",
            {
                "run_id": child.run_id,
                "task_id": task.task_id,
                "provider": task.provider,
                "role": task.role,
                "capability_profile": task.capability_profile,
                "depth": child.depth,
            },
        )
        if task.provider == "fork":
            child.session_history = [*copy.deepcopy(snapshot.session_history), *self._trajectory_messages(snapshot.trajectory)]
        if dependency_results:
            child.session_history.append(
                {
                    "role": "user",
                    "content": "Dependency results (structured facts only): "
                    + repr([result.to_dict() for result in dependency_results]),
                }
            )
        if cancel_event is not None:
            child._cancel_token = cancel_event  # type: ignore[attr-defined]
        self._event(parent, "child/start", {"task_id": task.task_id, "child_run_id": child.run_id, "workflow_id": workflow_id, "provider": task.provider})
        self._event(parent, "subagent/start", {"task_id": task.task_id, "child_run_id": child.run_id, "workflow_id": workflow_id})
        started = time.monotonic()
        child_cancel = threading.Event()
        timeout_fired = threading.Event()
        def trigger_timeout() -> None:
            timeout_fired.set()
            child_cancel.set()
            child_log = getattr(child, "_event_log", None)
            if child_log is not None:
                try:
                    child_log.append("cancel", {"run_id": child.run_id, "reason": "timeout"})
                except Exception:
                    pass
        timer = threading.Timer(task.timeout_sec, trigger_timeout) if task.timeout_sec > 0 else None
        if timer is not None:
            timer.daemon = True
            timer.start()
        child._cancel_token = child_cancel  # type: ignore[attr-defined]
        watcher: threading.Thread | None = None
        watcher_stop = threading.Event()
        if cancel_event is not None:
            def propagate_cancel() -> None:
                while not watcher_stop.wait(0.05):
                    if cancel_event.is_set():
                        child_cancel.set()
                        return
            watcher = threading.Thread(target=propagate_cancel, daemon=True)
            watcher.start()
        lease = None
        lease_epoch = None
        try:
            # Scouts are read-only and can overlap.  Any worker-capable child
            # acquires the workspace lease for its full lifetime, serializing
            # writes across sibling agents and preventing workflow races.
            if task.capability_profile != "scout":
                lease, lease_epoch = WorkspaceLeaseRegistry.acquire(child.workspace_host_path)
                child.lease_epoch = lease_epoch
            result = self.loop_factory(child).run(child)
            status = self._status(child.status)
            if task.max_turns > 0 and int(child.metrics.get("turns", 0)) > task.max_turns:
                status = "timeout"
            summary = child.final_answer or child.state_summary or ""
            facts = self.fact_extractor(child, result.trajectory)
            usage = {key: child.metrics.get(key) for key in ("turns", "prompt_tokens", "completion_tokens", "total_tokens")}
            outcome = ChildResult(
                task.task_id,
                child.run_id,
                status,
                task.role,
                summary=summary,
                facts=facts,
                artifacts=[str(child.run_dir / "events.jsonl")] if child.run_dir else [],
                usage=usage,
                failure=None if status == "completed" else child.state_summary,
            )
            if status == "timeout" and not outcome.failure:
                outcome.failure = f"child exceeded max_turns={task.max_turns}"
        except Exception as exc:  # child failures become data, never parent exceptions
            child.status = "failed"
            outcome = ChildResult(
                task.task_id,
                child.run_id,
                "failed",
                task.role,
                artifacts=[str(child.run_dir / "events.jsonl")] if child.run_dir else [],
                failure=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if timer is not None:
                timer.cancel()
            child_cancel.set()
            watcher_stop.set()
            if lease is not None:
                lease.release()
        if timeout_fired.is_set():
            outcome.status = "timeout"
            outcome.failure = f"child exceeded timeout_sec={task.timeout_sec}"
        elif cancel_event is not None and cancel_event.is_set():
            outcome.status = "cancelled"
            outcome.failure = "cancelled by caller"
        if task.timeout_sec > 0 and time.monotonic() - started > task.timeout_sec and outcome.status == "completed":
            outcome.status = "timeout"
            outcome.failure = f"child exceeded timeout_sec={task.timeout_sec}"
        self._event(parent, "child/result", outcome.to_dict())
        self._event(parent, "subagent/end", {"task_id": task.task_id, "child_run_id": child.run_id, "workflow_id": workflow_id, "status": outcome.status, "failure": outcome.failure})
        child_log = getattr(child, "_event_log", None)
        if child_log is not None:
            # The child loop normally closes these.  This covers failures before
            # the first model step and keeps the child log structurally replayable.
            try:
                child_log.append("session/end-seed", {"status": outcome.status, "failure": outcome.failure})
            except Exception:
                pass
        return outcome

    @staticmethod
    def _resolve_future(future: Any, task: ChildTask) -> ChildResult:
        return future.result()

    def _settle_parent(self, parent: RunState, results: list[ChildResult], workflow_id: str) -> None:
        parent.child_results.extend(result.to_dict() for result in results)
        for result in results:
            parent.facts.extend(asdict(fact) for fact in result.facts)
        summary = {"workflow_id": workflow_id, "status": "completed" if all(r.status == "completed" for r in results) else "partial", "children": [r.to_dict() for r in results]}
        self._event(parent, "workflow/summary", summary)

    @staticmethod
    def _status(status: str) -> ChildStatus:
        return status if status in {"completed", "failed", "interrupted", "cancelled", "timeout"} else "failed"  # type: ignore[return-value]

    def _validate(self, parent: RunState, tasks: list[ChildTask], join: JoinMode) -> None:
        if join not in {"all", "any"}:
            raise MultiAgentError("join must be 'all' or 'any'")
        ids = [task.task_id for task in tasks]
        if len(ids) != len(set(ids)) or any(not task.task_id.strip() for task in tasks):
            raise MultiAgentError("task ids must be non-empty and unique")
        known = set(ids)
        for task in tasks:
            if any(dep not in known for dep in task.depends_on):
                raise MultiAgentError(f"task {task.task_id!r} has an unknown dependency")
            if task.max_turns < 0 or task.timeout_sec < 0:
                raise MultiAgentError(f"task {task.task_id!r} has an invalid budget")
            if task.capability_profile not in {"root", "worker", "scout", "planner", "reviewer"}:
                raise MultiAgentError(f"unsupported capability profile: {task.capability_profile}")
        if parent.depth + 1 > self.max_depth:
            raise MultiAgentError(f"delegation depth {parent.depth + 1} exceeds max_depth={self.max_depth}")
        graph = {task.task_id: set(task.depends_on) for task in tasks}
        while graph:
            leaves = {key for key, deps in graph.items() if not deps}
            if not leaves:
                raise MultiAgentError("dependency cycle detected")
            graph = {key: deps - leaves for key, deps in graph.items() if key not in leaves}

    @staticmethod
    def _trajectory_messages(trajectory: tuple[TrajectoryStep, ...]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for step in trajectory:
            if step.action is not None:
                messages.append({"role": "assistant", "content": action_to_dict(step.action).__repr__()})
            messages.append({"role": "user", "content": f"Observation: {step.observation.message or step.observation.stdout_preview}"})
        return messages

    @staticmethod
    def _default_facts(state: RunState, trajectory: list[TrajectoryStep]) -> list[Fact]:
        summary = (state.final_answer or "").strip()
        return [Fact(summary)] if summary else []

    def _event(self, parent: RunState, event_type: str, data: dict[str, Any]) -> None:
        if self.trace is not None:
            trace_name = {
                "child/start": "child_start",
                "child/result": "child_result",
                "workflow/summary": "workflow_summary_observation",
            }.get(event_type, event_type.replace("/", "_"))
            self.trace.record(trace_name, parent, **data)
        log = getattr(parent, "_event_log", None)
        if log is not None:
            try:
                log.append(event_type, data)
            except Exception:
                pass


__all__ = ["ChildResult", "ChildTask", "Evidence", "Fact", "ForkSnapshot", "JobRecord", "JobRegistry", "MultiAgentError", "MultiAgentManager", "WorkspaceLeaseRegistry"]
