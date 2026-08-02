from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from minicc.core.state import RunState
from minicc.trace.metrics import write_metrics
from minicc.trace.recorder import TraceRecorder
from minicc.trace.report import write_run_report
from minicc.memory.working import write_working_memory_snapshot


@dataclass
class RunLifecycle:
    trace: TraceRecorder
    _started_at: float | None = None
    _base_duration_ms: int = 0

    def start(self, state: RunState) -> None:
        self._started_at = time.perf_counter()
        self._base_duration_ms = 0
        state.metrics["started_at"] = datetime.now(timezone.utc).isoformat()
        self.trace.run_started(state)

    def resume(self, state: RunState, trajectory_steps: int) -> None:
        self._started_at = time.perf_counter()
        self._base_duration_ms = int(state.metrics.get("total_duration_ms", 0))
        self.trace.run_resumed(state, trajectory_steps)

    def finish(self, state: RunState) -> None:
        state.metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
        if self._started_at is not None:
            state.metrics["total_duration_ms"] = self._base_duration_ms + int(
                (time.perf_counter() - self._started_at) * 1000
            )
        if state.status == "completed":
            memory_path = write_working_memory_snapshot(state)
            if memory_path is not None:
                self.trace.working_memory_captured(
                    state,
                    memory_path,
                    len(state.memory_references),
                )
            self.trace.run_completed(state)
        elif state.status == "interrupted":
            self.trace.run_interrupted(state, int(state.metrics.get("interrupted_after_steps", 0)))
        elif state.status == "failed":
            self.trace.run_failed(state)
        write_metrics(state)
        write_run_report(state)
