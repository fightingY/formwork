"""Durable asynchronous memory projection worker.

The worker is intentionally small: SQLite owns the queue state, while the
callable supplied by the harness performs the semantic L1/L2/L3 projection.
Jobs can therefore be resumed after a process restart without changing the
canonical EventLog.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from minicc.memory.l1 import MemoryStore


class MemoryWorker:
    def __init__(self, store: MemoryStore, processor: Callable[[dict], None], *, poll_interval: float = 0.25) -> None:
        self.store = store
        self.processor = processor
        self.poll_interval = max(0.01, poll_interval)
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

    def flush(self, *, max_jobs: int = 100) -> int:
        processed = 0
        while processed < max_jobs:
            jobs = self.store.claim_jobs(limit=1)
            if not jobs:
                break
            self._process(jobs[0])
            processed += 1
        return processed

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.flush(max_jobs=1) == 0:
                self._stop.wait(self.poll_interval)

    def _process(self, job: dict) -> None:
        try:
            self.processor(job)
        except Exception as exc:  # noqa: BLE001 - failed memory must not block the run
            self.store.fail_job(int(job["job_id"]), f"{type(exc).__name__}: {exc}", retry=True)
        else:
            self.store.complete_job(int(job["job_id"]))


__all__ = ["MemoryWorker"]
