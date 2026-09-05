"""Durable asynchronous memory projection worker (spec §6).

SQLite owns the queue state and the worker owns no in-memory job bookkeeping:
each claim leases a ``memory_jobs`` row (owner token + expiry), the processor
rebuilds the turn evidence purely from the row's EventLog range, and completion
or failure is written back to the same row.  A restarted worker therefore picks
up exactly where the crashed one left off — ``recover_stale_jobs`` re-queues
anything whose lease lapsed, and ``run_once``/``flush`` give tests and the CLI
a synchronous path.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from minicc.memory.l1 import DEFAULT_JOB_MAX_ATTEMPTS, MemoryStore


class MemoryWorker:
    def __init__(
        self,
        store: MemoryStore,
        processor: Callable[[dict], None],
        *,
        poll_interval: float = 0.25,
        stale_after_sec: int = 900,
        max_attempts: int = DEFAULT_JOB_MAX_ATTEMPTS,
    ) -> None:
        self.store = store
        self.processor = processor
        self.poll_interval = max(0.01, poll_interval)
        self.stale_after_sec = max(1, stale_after_sec)
        self.max_attempts = max(1, max_attempts)
        self.owner = uuid.uuid4().hex[:12]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="minicc-memory-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def recover_stale_jobs(self) -> int:
        """Re-queue jobs whose lease expired (crashed worker recovery)."""
        return self.store.recover_stale_jobs(stale_after_sec=self.stale_after_sec)

    def run_once(self, *, limit: int = 1) -> int:
        """Process at most ``limit`` jobs synchronously; return how many ran."""
        jobs = self.store.claim_jobs(
            limit=max(0, limit), stale_after_sec=self.stale_after_sec, owner=self.owner
        )
        for job in jobs:
            self._process(job)
        return len(jobs)

    def flush(self, *, max_jobs: int = 100) -> int:
        processed = 0
        while processed < max_jobs and self.run_once(limit=1) > 0:
            processed += 1
        return processed

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.run_once(limit=1) == 0:
                self._stop.wait(self.poll_interval)

    def _process(self, job: dict) -> None:
        try:
            self.processor(job)
        except Exception as exc:  # noqa: BLE001 - failed memory must not block the run
            self.store.fail_job(
                int(job["job_id"]),
                f"{type(exc).__name__}: {exc}",
                retry=True,
                max_attempts=self.max_attempts,
            )
        else:
            self.store.complete_job(int(job["job_id"]))


__all__ = ["MemoryWorker"]
