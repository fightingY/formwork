from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from minicc.core.state import RunState
from minicc.trace.metrics import write_metrics
from minicc.trace.recorder import TraceRecorder


@dataclass
class RunLifecycle:
    trace: TraceRecorder
    _started_at: float | None = None

    def start(self, state: RunState) -> None:
        self._started_at = time.perf_counter()
        state.metrics["started_at"] = datetime.now(timezone.utc).isoformat()
        self.trace.run_started(state)

    def finish(self, state: RunState) -> None:
        state.metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
        if self._started_at is not None:
            state.metrics["total_duration_ms"] = int((time.perf_counter() - self._started_at) * 1000)
        if state.status == "completed":
            self.trace.run_completed(state)
        elif state.status == "failed":
            self.trace.run_failed(state)
        write_metrics(state)
