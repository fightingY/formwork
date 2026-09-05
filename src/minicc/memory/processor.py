"""Durable memory-job processing (spec §6).

``MemoryJobProcessor`` turns one ``memory_jobs`` row into a full L1→L2/L3
projection pass.  It owns **no closures and no live-run state**: everything it
needs comes from the job row itself (session/run/project ids, the EventLog seq
range, and a convenience payload), so a worker that just started — or a
completely different process — can finish a job another worker enqueued before
crashing.  Failures propagate to the caller (:class:`MemoryWorker`) which owns
the retry/lease bookkeeping.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from minicc.core.events import EventLog
from minicc.memory.evidence import EventEvidenceReader
from minicc.memory.l1 import (
    L1Distiller,
    MemoryProjector,
    MemoryStore,
    ProjectionInput,
)


class MemoryJobProcessor:
    """Process one durable memory job through the shared projection pipeline."""

    def __init__(
        self,
        store: MemoryStore,
        distiller: L1Distiller,
        *,
        deduper: Any = None,
        escalator: Any = None,
    ) -> None:
        self.store = store
        self.distiller = distiller
        self.deduper = deduper
        self.escalator = escalator
        self.projector = MemoryProjector(
            store, distiller, deduper=deduper, escalator=escalator
        )

    def process(self, job: dict[str, Any]) -> None:
        """Project one job; raises only so the worker can retry/fail it."""
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        payload = payload or {}
        seq_start = int(job.get("source_seq_start") or 0)
        seq_end = int(job.get("source_seq_end") or 0)
        event_log_path = Path(str(payload.get("event_log_path") or ""))

        evidence = EventEvidenceReader(event_log_path).read(seq_start, seq_end) if (
            event_log_path.exists()
        ) else None
        # Payload text wins when present (it is the live turn's exact wording);
        # the EventLog rebuild is the durable fallback that makes restarts work.
        user_message = str(payload.get("user_message") or (evidence.user_message if evidence else ""))
        assistant_reply = str(
            payload.get("assistant_reply") or (evidence.assistant_reply if evidence else "")
        )
        run_facts = str(payload.get("run_facts") or (evidence.run_facts if evidence else ""))

        session_id = str(job.get("session_id") or "")
        run_id = str(job.get("source_run_id") or "")
        project_id = str(job.get("project_id") or "project")
        event_range = (seq_start, seq_end) if seq_end > 0 else None

        # Shim "state": fresh metrics (the original run is gone) plus the
        # session EventLog as the memory-lifecycle event sink, so projected
        # memory/... audit events still land in L0.
        state = SimpleNamespace(
            metrics={},
            run_id=run_id,
            session_id=session_id,
            project_id=project_id,
            _event_log=EventLog(event_log_path) if event_log_path.exists() else None,
        )
        self.projector.project(
            ProjectionInput(
                session_id=session_id,
                run_id=run_id,
                project_id=project_id,
                user_message=user_message,
                assistant_reply=assistant_reply,
                run_facts=run_facts,
                event_range=event_range,
                state=state,
            )
        )

    # Callable alias so MemoryWorker accepts the processor directly.
    __call__ = process
