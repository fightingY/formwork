"""Deterministic tests for the recoverable memory_jobs queue (spec §6).

The queue must survive a worker restart: the durable ``memory_jobs`` row (plus
its EventLog seq range) is the only input a processor needs — no closures, no
live-run state.  These tests exercise enqueue → claim → retry → terminal and
prove a brand-new store/processor/worker can finish a job another one enqueued.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from types import SimpleNamespace

from minicc.core.events import EventLog
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import RunState
from minicc.memory.l1 import L1Distiller, L1Memory, MemoryStore, MemoryTurnHook
from minicc.memory.processor import MemoryJobProcessor
from minicc.memory.worker import MemoryWorker


@dataclass
class ScriptedProvider:
    replies: list[str]
    seen: list[list[dict[str, str]]] = field(default_factory=list)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        self.seen.append(messages)
        return ModelResponse(
            text=self.replies.pop(0),
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
        )


def _store(tmp_path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    return store


# --- queue primitives --------------------------------------------------------


def test_memory_job_enqueue(tmp_path) -> None:
    store = _store(tmp_path)
    job_id = store.enqueue_job(
        project_id="proj",
        session_id="sess-1",
        source_run_id="run-1",
        source_seq_start=3,
        source_seq_end=9,
        payload={"user_message": "hi", "event_log_path": "events.jsonl"},
    )

    assert isinstance(job_id, int)
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
    assert row["project_id"] == "proj"
    assert row["session_id"] == "sess-1"
    assert row["source_run_id"] == "run-1"
    assert (row["source_seq_start"], row["source_seq_end"]) == (3, 9)
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert json.loads(row["payload_json"])["user_message"] == "hi"


def test_memory_job_claim(tmp_path) -> None:
    store = _store(tmp_path)
    first_id = store.enqueue_job(project_id="p", payload={})
    second_id = store.enqueue_job(project_id="p", payload={})

    claimed = store.claim_jobs(limit=1, owner="worker-a")
    assert [job["job_id"] for job in claimed] == [first_id]
    job = claimed[0]
    assert job["status"] == "running"
    assert job["attempts"] == 1
    assert job["lease_owner"] == "worker-a"
    assert job["lease_expires_at"]  # lease stamped so a crashed claim can expire

    # The claimed job is not handed out twice; the next claim gets the other one.
    again = store.claim_jobs(limit=1, owner="worker-a")
    assert [job["job_id"] for job in again] == [second_id]


def test_memory_job_retry(tmp_path) -> None:
    store = _store(tmp_path)
    job_id = store.enqueue_job(project_id="p", payload={})

    # Attempts 1..2 are re-queued; the third failure spends the budget.
    claimed = store.claim_jobs(limit=1)
    assert claimed[0]["job_id"] == job_id
    assert store.fail_job(job_id, "boom 1", retry=True, max_attempts=3) is True
    assert store.claim_jobs(limit=1)[0]["attempts"] == 2
    assert store.fail_job(job_id, "boom 2", retry=True, max_attempts=3) is True

    assert store.claim_jobs(limit=1)[0]["attempts"] == 3
    assert store.fail_job(job_id, "boom 3", retry=True, max_attempts=3) is False

    with sqlite3.connect(str(store.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"  # terminal: never claimed again
    assert "boom 3" in row["last_error"]
    assert store.claim_jobs(limit=5) == []

    # The worker surfaces the same terminal behaviour through run_once.
    store2 = _store(tmp_path / "worker")
    store2.enqueue_job(project_id="p", payload={})

    def failing_processor(job: dict) -> None:
        raise RuntimeError("distiller exploded")

    worker = MemoryWorker(store2, failing_processor, max_attempts=3)
    assert worker.run_once() == 1
    assert worker.run_once() == 1
    assert worker.run_once() == 1
    assert worker.run_once() == 0  # budget spent, nothing left to claim
    with sqlite3.connect(str(store2.db_path)) as conn:
        status = conn.execute("SELECT status FROM memory_jobs").fetchone()[0]
    assert status == "failed"


def test_stale_running_job_recovered(tmp_path) -> None:
    """A worker that died mid-job leaves a running row; after the lease lapses
    any later worker sweeps it back to pending and finishes it (spec §6)."""
    store = _store(tmp_path)
    job_id = store.enqueue_job(project_id="p", payload={})
    store.claim_jobs(limit=1, owner="dead-worker")

    # Simulate the lease lapsing (a crashed worker never renewed it).
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE memory_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE job_id=?",
            (job_id,),
        )
        conn.commit()

    assert store.recover_stale_jobs() == 1
    recovered = store.claim_jobs(limit=1, owner="new-worker")
    assert [job["job_id"] for job in recovered] == [job_id]
    assert recovered[0]["lease_owner"] == "new-worker"


# --- restart recovery through the real pipeline ------------------------------


def _write_turn_log(path) -> tuple[EventLog, int, int]:
    """A real events.jsonl holding one turn: user ask + bash run + final reply."""
    log = EventLog(path)
    turn_start = log.append("turn/start", {"turn": 1})
    log.append("user/message", {"turn": 1, "content": "why does test_worker fail?"})
    log.append(
        "tool/call",
        {"turn": 1, "step": 1, "call_id": "c1", "tool": "bash",
         "arguments": {"command": "uv run pytest tests/test_worker.py -q"}},
    )
    log.append(
        "tool/result",
        {"turn": 1, "step": 1, "call_id": "c1", "tool": "bash", "is_error": False,
         "content": "1 failed: test_worker_timeout"},
    )
    log.append(
        "assistant/message",
        {"turn": 1, "message": {"content": "the worker ignores poll_interval"}},
    )
    return log, turn_start.seq, log.last_seq


def test_worker_restart_uses_event_range(tmp_path) -> None:
    """The restart contract: enqueue, throw away every live object, then let a
    brand-new store + processor finish the job purely from the durable row."""
    db = tmp_path / "memory" / "project.db"
    log_path = tmp_path / "sessions" / "s1" / "events.jsonl"
    log, seq_start, seq_end = _write_turn_log(log_path)

    # The enqueuing process records only the EventLog anchor — its payload
    # carries no distilled text, so recovery MUST rebuild from the log.
    store = MemoryStore(db)
    store.initialize()
    store.enqueue_job(
        project_id="proj",
        session_id="s1",
        source_run_id="run-1",
        source_seq_start=seq_start,
        source_seq_end=seq_end,
        payload={"event_log_path": str(log_path)},
    )

    # --- restart: fresh objects, nothing shared with the enqueueing process ---
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {
                        "type": "fact",
                        "content": "the memory worker ignores poll_interval on restart",
                        "priority": 60,
                        "scope": "project",
                        "source": {"file": "src/minicc/memory/worker.py"},
                    }
                ]
            )
        ]
    )
    fresh_store = MemoryStore(db)
    fresh_store.initialize()
    processor = MemoryJobProcessor(fresh_store, L1Distiller(provider))
    worker = MemoryWorker(fresh_store, processor)
    assert worker.flush() == 1

    memories = fresh_store.list_memories()
    assert [memory.content for memory in memories] == [
        "the memory worker ignores poll_interval on restart"
    ]
    # Provenance points back at the EventLog range the job was built from.
    assert memories[0].source["event_seq_start"] == seq_start
    assert memories[0].source["event_seq_end"] == seq_end
    assert memories[0].source_run_id == "run-1"

    # The evidence really was rebuilt from the log (user ask + tool transcript).
    prompt = provider.seen[0][1]["content"]
    assert "why does test_worker fail?" in prompt
    assert "uv run pytest tests/test_worker.py -q" in prompt
    assert "test_worker_timeout" in prompt

    # The generation row records the L1 pass over the same seq range.
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        generation = conn.execute(
            "SELECT * FROM memory_generations WHERE layer='L1'"
        ).fetchone()
        job = conn.execute("SELECT * FROM memory_jobs").fetchone()
    assert (generation["source_seq_start"], generation["source_seq_end"]) == (seq_start, seq_end)
    assert generation["status"] == "completed"
    assert job["status"] == "completed"

    # The audit event landed back in the same append-only log (L0 stays canonical).
    types = [event.type for event in EventLog(log_path).events]
    assert "memory/l1_extracted" in types
    assert "memory/l2_upserted" not in types  # no scenario: nothing to escalate


# --- failure isolation (spec: 记忆失败不阻断任务) -----------------------------


def test_memory_failure_does_not_fail_turn(tmp_path) -> None:
    class BrokenProvider:
        def complete(self, messages, *, options=None):
            raise RuntimeError("upstream down")

    store = _store(tmp_path)
    state = RunState.start("do the task")
    state._event_log = EventLog(tmp_path / "events.jsonl")
    hook = MemoryTurnHook(store, L1Distiller(BrokenProvider()))
    result = SimpleNamespace(state=state, user_message="u", assistant_reply="a", run_id="r1")

    hook("sess-1", result)  # must not raise

    assert state.metrics["memory_distill_failed"] == 1
    assert store.count_memories() == 0

    # Background mode fails just as softly: the job is durably marked failed.
    store2 = _store(tmp_path / "bg")
    store2.enqueue_job(project_id="p", source_seq_end=4, payload={})
    processor = MemoryJobProcessor(store2, L1Distiller(BrokenProvider()))
    worker = MemoryWorker(store2, processor)
    worker.run_once()  # provider failure degrades to a failed generation, not a crash
    with sqlite3.connect(str(store2.db_path)) as conn:
        status = conn.execute("SELECT status FROM memory_jobs").fetchone()[0]
    assert status == "completed"
    with sqlite3.connect(str(store2.db_path)) as conn:
        generation_status = conn.execute(
            "SELECT status FROM memory_generations WHERE layer='L1'"
        ).fetchone()[0]
    assert generation_status == "failed"


def test_background_hook_enqueues_durable_job(tmp_path) -> None:
    """background=true turns the turn-end hook into enqueue-only: the job lands
    in SQLite (recoverable by any worker), never in an in-memory closure."""
    store = _store(tmp_path)
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    turn_start = log.append("turn/start", {"turn": 1})
    log.append("user/message", {"turn": 1, "content": "remember the deploy command"})

    state = RunState.start("remember the deploy command")
    state._event_log = log
    state.metrics["turn_start_seq"] = turn_start.seq
    stub_worker = SimpleNamespace(start=lambda: None)  # never started in tests
    hook = MemoryTurnHook(
        store, L1Distiller(ScriptedProvider([])), background=True, worker=stub_worker
    )
    result = SimpleNamespace(
        state=state,
        user_message="remember the deploy command",
        assistant_reply="a",
        run_id="run-1",
    )

    hook("sess-1", result)

    assert state.metrics["memory_job_id"] == 1
    assert store.count_memories() == 0  # nothing distilled inline
    job = store.claim_jobs(limit=1)[0]
    assert job["source_seq_start"] == turn_start.seq
    assert job["payload"]["event_log_path"] == str(log_path)
    assert "remember the deploy command" in job["payload"]["user_message"]
    assert "memory/capture_requested" in [event.type for event in log.events]


# --- memory payload shapes (defensive round trip) ----------------------------


def test_job_payload_memories_are_json_safe(tmp_path) -> None:
    store = _store(tmp_path)
    memory = L1Memory(type="fact", content="x", priority=1, scope="project")
    job_id = store.enqueue_job(project_id="p", payload={"memory": memory.to_dict()})

    claimed = store.claim_jobs(limit=1)[0]
    assert claimed["job_id"] == job_id
    assert claimed["payload"]["memory"]["content"] == "x"
