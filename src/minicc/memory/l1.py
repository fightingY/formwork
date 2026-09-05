"""L1 atomic memory: schema, SQLite/FTS5 store, distillation, and recall.

V5.1 memory redesign (``docs/V5_1_MEMORY_REDESIGN_PLAN.md``) P0.  L1 is the
first *semantic* layer of the L0→L3 pyramid: every committed session turn is
distilled by one ``json_mode`` LLM call into a handful of atomic memories
(``{type, content, priority, scope, source}``), stored in a per-project SQLite
database with an FTS5 (BM25) index, and recalled at the start of the next turn
into a ``<relevant-memories>`` block at the top of the context.

The whole layer is *best effort*: every entry point degrades to a metric or an
empty result rather than raising, so a memory failure can never turn a run into
a failure (plan §4.5).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from minicc.core.provider import CompletionOptions, ModelProvider, ModelUsage, ProviderError

MemoryType = Literal["fact", "preference", "decision", "constraint", "todo"]
MemoryScope = Literal["project", "session"]

MEMORY_TYPES: frozenset[str] = frozenset(
    {"fact", "preference", "decision", "constraint", "todo"}
)
MEMORY_SCOPES: frozenset[str] = frozenset({"project", "session"})

# Default recall/bootstrap budget (plan §4.4).
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_CHARS_PER_MEMORY = 500
DEFAULT_MAX_TOTAL_CHARS = 4_000
DEFAULT_RECALL_TIMEOUT_SEC = 5.0
DEFAULT_MAX_SCENARIOS = 5
DEFAULT_DEDUP_CANDIDATES = 5

# L1 validation bounds: model output is untrusted, so oversized content is
# dropped rather than truncated into a misleading half-fact.
MAX_CONTENT_CHARS = 2_000
DEFAULT_TOPIC_MAX_LENGTH = 120

# Recall candidate pool per strategy before fusion/rerank/budget trim.
RECALL_CANDIDATE_POOL = 50

# Rerank boosts on top of the fused ranking (see ``rerank_score``).
RERANK_PRIORITY_WEIGHT = 0.3
RERANK_CONFIDENCE_WEIGHT = 0.2
RERANK_RECENCY_BOOST = 0.1
RERANK_RECENCY_DAYS = 7
RERANK_STALE_PENALTY = 0.1
RERANK_STALE_TODO_DAYS = 14

# Background job retry budget (memory_jobs.attempts); configurable via
# ``memory.job_max_attempts``.
DEFAULT_JOB_MAX_ATTEMPTS = 3

# Optional local embedding back-end (plan §4.3/§4.4).  A plain callable keeps
# the store free of any hard vector-library dependency: inject ``text -> vec``,
# or leave it ``None`` and every search falls back to pure BM25.
Embedder = Callable[[str], list[float]]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def project_db_path(project_root: Path | str, *, memory_root: Path | None = None) -> Path:
    """One SQLite database per project: ``<memory_root>/<project-hash>.db``."""
    root = memory_root or (Path.cwd() / ".minicc" / "memory")
    digest = hashlib.sha256(
        str(Path(project_root).resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return root / f"{digest}.db"


@dataclass
class L1Memory:
    """One atomic memory row.

    ``record_id`` is ``None`` until the row is persisted.  ``source`` keeps a
    traceable ``{run_id, event_seq_start, event_seq_end, file, line}`` reference
    back to where the memory came from (plan §4.2); it is provenance, not the
    working-memory hash ceremony.  ``topic_key`` is persisted at write time so
    L2 clustering queries are plain SQL instead of ad-hoc re-derivation, and
    ``status``/``supersedes_record_id`` carry the dedup lifecycle
    (``active`` → ``superseded`` when an update changes the topic).
    """

    type: MemoryType
    content: str
    priority: int
    scope: MemoryScope
    session_id: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    record_id: int | None = None
    topic_key: str = ""
    confidence: float = 0.5
    status: str = "active"
    source_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "type": self.type,
            "content": self.content,
            "priority": self.priority,
            "scope": self.scope,
            "session_id": self.session_id,
            "topic_key": self.topic_key,
            "confidence": self.confidence,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> L1Memory:
        return cls(
            record_id=int(row["record_id"]),
            type=row["type"],
            content=row["content"],
            priority=int(row["priority"]),
            scope=row["scope"],
            session_id=row["session_id"],
            topic_key=str(row["topic_key"] or ""),
            confidence=float(row["confidence"]) if row["confidence"] is not None else 0.5,
            status=str(row["status"] or "active"),
            source_run_id=str(row["source_run_id"] or ""),
            source=_loads_json(row["source_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_loads_json(row["metadata_json"], {}),
        )


@dataclass
class PersonaEntry:
    """One L3 rule row (``persona`` table).

    A row carries exactly one rule in whichever of ``profile`` / ``style`` /
    ``hard_rule`` is filled.  ``rule_key`` is the stable identity of the rule
    text, so re-synthesis of the same rule updates its row instead of piling up.
    ``state`` is the candidate/confirmed lifecycle (V5.1 §5): a synthesized rule
    starts as ``candidate`` and only becomes ``confirmed`` via recurrence or an
    explicit user confirmation — a candidate is never injected into prompts.
    """

    profile: str = ""
    style: str = ""
    hard_rule: str = ""
    source_record_ids: list[int] = field(default_factory=list)
    origin: str = "auto"
    confidence: float = 0.0
    updated_at: str = ""
    persona_id: int | None = None
    rule_key: str = ""
    state: str = "candidate"
    confirmation_count: int = 0
    confirmed_at: str = ""
    created_at: str = ""
    generation_id: int | None = None

    def rule_field(self) -> str:
        """Which persona facet this rule came from (``""`` when empty)."""
        if self.hard_rule.strip():
            return "hard_rule"
        if self.profile.strip():
            return "profile"
        if self.style.strip():
            return "style"
        return ""

    def rule_text(self) -> str:
        return {"hard_rule": self.hard_rule, "profile": self.profile, "style": self.style}.get(
            self.rule_field(), ""
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PersonaEntry:
        return cls(
            persona_id=int(row["persona_id"]),
            profile=row["profile"],
            style=row["style"],
            hard_rule=row["hard_rule"],
            source_record_ids=_loads_json(row["source_record_ids"], []),
            origin=row["origin"],
            confidence=float(row["confidence"]),
            updated_at=row["updated_at"],
            rule_key=str(row["rule_key"] or ""),
            state=str(row["state"] or "candidate"),
            confirmation_count=int(row["confirmation_count"] or 0),
            confirmed_at=str(row["confirmed_at"] or ""),
            created_at=str(row["created_at"] or ""),
            generation_id=row["generation_id"],
        )


@dataclass
class ScenarioEntry:
    """One L2 scenario row (``scenarios`` table).

    ``scenario`` names the topic/component; ``summary`` captures the distilled
    knowledge; ``recipe`` is the reusable fix/run path, stored as a JSON array
    of steps (see :func:`recipe_steps`) so it can later be executed, not just
    read (plan §3).  ``topic_key`` is the clustering identity — upserts are
    keyed by it, not by the free-form scenario name.  ``doc_ref`` may later
    point at a CLAUDE.md paragraph as the human-written seed (§3.1).
    """

    scenario: str = ""
    summary: str = ""
    recipe: str = ""
    source_record_ids: list[int] = field(default_factory=list)
    doc_ref: str = ""
    updated_at: str = ""
    scenario_id: int | None = None
    topic_key: str = ""
    confidence: float = 0.0
    status: str = "active"
    created_at: str = ""
    generation_id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ScenarioEntry:
        return cls(
            scenario_id=int(row["scenario_id"]),
            scenario=row["scenario"],
            summary=row["summary"],
            recipe=row["recipe"],
            source_record_ids=_loads_json(row["source_record_ids"], []),
            doc_ref=row["doc_ref"],
            updated_at=row["updated_at"],
            topic_key=str(row["topic_key"] or ""),
            confidence=float(row["confidence"]) if row["confidence"] is not None else 0.0,
            status=str(row["status"] or "active"),
            created_at=str(row["created_at"] or ""),
            generation_id=row["generation_id"],
        )


def recipe_steps(entry: ScenarioEntry) -> list[str]:
    """Decode a scenario's ``recipe`` column into ordered steps.

    The column stores a JSON array of step strings; legacy rows (and manual
    seeds) may hold a plain sentence, which degrades to a single step.
    """
    data = _loads_json(entry.recipe, None)
    if isinstance(data, list):
        return [str(step).strip() for step in data if str(step).strip()]
    text = entry.recipe.strip()
    return [text] if text else []


def _loads_json(raw: Any, default: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


_MAIN_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT 'project',
    session_id TEXT NOT NULL DEFAULT '',
    topic_key TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',
    last_confirmed_at TEXT NOT NULL DEFAULT '',
    supersedes_record_id INTEGER,
    source_run_id TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    embedding BLOB
)
"""

# Standalone (not external-content) FTS table: ``record_id`` is an UNINDEXED
# payload column that maps 1:1 onto ``memories.record_id``; the index is kept in
# step by MemoryStore on write, so we never depend on FTS5 index-rebuild magic.
_FTS_TABLE_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    record_id UNINDEXED,
    content,
    type,
    tokenize='unicode61'
)
"""

# L2/L3 tables are created from P0 (plan §4.3) but only filled in P1/P2.
_SCENARIOS_DDL = """
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key TEXT NOT NULL DEFAULT '',
    scenario TEXT NOT NULL,
    summary TEXT NOT NULL,
    recipe TEXT NOT NULL DEFAULT '',
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    doc_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active',
    generation_id INTEGER,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)
"""

# L1 ↔ L2 membership is the real relation; ``scenarios.source_record_ids`` is a
# redundant read-side copy kept for cheap rendering.
_SCENARIO_MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS scenario_members (
    scenario_id INTEGER NOT NULL,
    record_id INTEGER NOT NULL,
    contribution TEXT NOT NULL DEFAULT '',
    rank INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scenario_id, record_id)
)
"""

_PERSONA_DDL = """
CREATE TABLE IF NOT EXISTS persona (
    persona_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '',
    hard_rule TEXT NOT NULL DEFAULT '',
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.0,
    state TEXT NOT NULL DEFAULT 'candidate',
    confirmation_count INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT NOT NULL DEFAULT '',
    generation_id INTEGER,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)
"""

_MEMORY_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS memory_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    source_run_id TEXT NOT NULL DEFAULT '',
    source_seq_start INTEGER NOT NULL DEFAULT 0,
    source_seq_end INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT ''
)
"""

_MEMORY_GENERATIONS_DDL = """
CREATE TABLE IF NOT EXISTS memory_generations (
    generation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL DEFAULT '',
    source_seq_start INTEGER NOT NULL DEFAULT 0,
    source_seq_end INTEGER NOT NULL DEFAULT 0,
    prompt_hash TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL DEFAULT '',
    output_hash TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    model_config_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    record_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
)
"""

_MEMORY_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS memory_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT ''
)
"""

_MEMORY_SNAPSHOT_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS memory_snapshot_items (
    snapshot_id INTEGER NOT NULL,
    layer TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, layer, item_id),
    FOREIGN KEY (snapshot_id) REFERENCES memory_snapshots(snapshot_id)
)
"""

# Columns added by schema v2 (``PRAGMA user_version`` 1 → 2).  ``initialize``
# widens v1 databases in place with these declarations; fresh databases already
# have them via the DDL above.
_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("memories", "topic_key TEXT NOT NULL DEFAULT ''"),
    ("memories", "confidence REAL NOT NULL DEFAULT 0.5"),
    ("memories", "status TEXT NOT NULL DEFAULT 'active'"),
    ("memories", "last_confirmed_at TEXT NOT NULL DEFAULT ''"),
    ("memories", "supersedes_record_id INTEGER"),
    ("memories", "source_run_id TEXT NOT NULL DEFAULT ''"),
    ("scenarios", "topic_key TEXT NOT NULL DEFAULT ''"),
    ("scenarios", "confidence REAL NOT NULL DEFAULT 0.0"),
    ("scenarios", "status TEXT NOT NULL DEFAULT 'active'"),
    ("scenarios", "generation_id INTEGER"),
    ("scenarios", "created_at TEXT NOT NULL DEFAULT ''"),
    ("persona", "rule_key TEXT NOT NULL DEFAULT ''"),
    ("persona", "state TEXT NOT NULL DEFAULT 'candidate'"),
    ("persona", "confirmation_count INTEGER NOT NULL DEFAULT 0"),
    ("persona", "confirmed_at TEXT NOT NULL DEFAULT ''"),
    ("persona", "generation_id INTEGER"),
    ("persona", "created_at TEXT NOT NULL DEFAULT ''"),
    ("memory_jobs", "lease_owner TEXT NOT NULL DEFAULT ''"),
    ("memory_jobs", "lease_expires_at TEXT NOT NULL DEFAULT ''"),
)


class MemoryStore:
    """Per-project SQLite + FTS5 storage for L1 (and, later, L2/L3) memories.

    Every method opens a fresh connection for its own operation so the store is
    safe to use from multiple threads (the web chat server runs turns on a
    ``ThreadingHTTPServer``) without shared-connection locking.
    """

    def __init__(self, db_path: Path, *, embedder: Embedder | None = None) -> None:
        self.db_path = db_path
        self.embedder = embedder

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            for ddl in (
                _MAIN_TABLE_DDL,
                _FTS_TABLE_DDL,
                _SCENARIOS_DDL,
                _SCENARIO_MEMBERS_DDL,
                _PERSONA_DDL,
                _MEMORY_JOBS_DDL,
                _MEMORY_GENERATIONS_DDL,
                _MEMORY_SNAPSHOTS_DDL,
                _MEMORY_SNAPSHOT_ITEMS_DDL,
            ):
                conn.execute(ddl)
            # Schema v2: explicit topic/confidence/status columns.  Fresh
            # databases get them from the DDL above; databases created by the
            # v1 schema are widened in place and backfilled below.
            for table, column_decl in _V2_COLUMNS:
                _ensure_column(conn, table, column_decl)
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version < 2:
                self._backfill_v2(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_jobs_status ON memory_jobs(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_snapshots_active ON memory_snapshots(project_id, status, snapshot_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(scope, topic_key, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_type_scope ON memories(type, scope, status)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at)")
            conn.execute("PRAGMA user_version = 2")

    @staticmethod
    def _backfill_v2(conn: sqlite3.Connection) -> None:
        """Backfill the v2 columns on rows written by the v1 schema.

        ``source_run_id`` comes straight from the provenance JSON; ``topic_key``
        needs the normalization logic, so it is derived row by row.
        """
        conn.execute(
            "UPDATE memories SET source_run_id = COALESCE(json_extract(source_json, '$.run_id'), '') "
            "WHERE source_run_id = '' AND source_json LIKE '%run_id%'"
        )
        rows = conn.execute(
            "SELECT record_id, source_json, metadata_json FROM memories WHERE topic_key = ''"
        ).fetchall()
        for row in rows:
            topic_key = derive_topic_key(
                _loads_json(row["source_json"], {}), _loads_json(row["metadata_json"], {})
            )
            if topic_key:
                conn.execute(
                    "UPDATE memories SET topic_key = ? WHERE record_id = ?",
                    (topic_key, int(row["record_id"])),
                )
        conn.execute(
            "UPDATE scenarios SET topic_key = lower(scenario) WHERE topic_key = ''"
        )

    def enqueue_job(
        self,
        *,
        project_id: str,
        session_id: str = "",
        source_run_id: str = "",
        source_seq_start: int = 0,
        source_seq_end: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Durably enqueue a turn-end projection job.

        The payload is deliberately bounded turn input, while the canonical
        event range remains the auditable source reference for a rebuild.
        """
        now = _now()
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO memory_jobs (project_id, session_id, source_run_id, "
                "source_seq_start, source_seq_end, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, session_id, source_run_id, int(source_seq_start), int(source_seq_end),
                 json.dumps(payload or {}, ensure_ascii=False), now),
            )
            job_id = cursor.lastrowid
            assert job_id is not None
            return int(job_id)

    def claim_jobs(
        self,
        *,
        limit: int = 1,
        stale_after_sec: int = 900,
        owner: str = "",
    ) -> list[dict[str, Any]]:
        """Atomically claim pending jobs, first recovering expired leases.

        A claimed job is stamped with ``lease_owner`` + ``lease_expires_at`` so a
        crashed worker's jobs are re-claimable after the lease lapses — the queue
        state lives entirely in SQLite, so any worker can pick the work up.
        """
        now = _now()
        owner = owner or uuid.uuid4().hex[:12]
        expires = (
            datetime.now().astimezone() + timedelta(seconds=max(1, stale_after_sec))
        ).isoformat(timespec="seconds")
        with self._connection() as conn:
            self._recover_stale(conn, now, stale_after_sec)
            rows = conn.execute(
                "SELECT * FROM memory_jobs WHERE status='pending' ORDER BY job_id LIMIT ?",
                (max(0, limit),),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                conn.execute(
                    "UPDATE memory_jobs SET status='running', attempts=attempts+1, "
                    "lease_owner=?, lease_expires_at=?, started_at=? WHERE job_id=?",
                    (owner, expires, now, int(row["job_id"])),
                )
                item = dict(row)
                # Reflect the claim itself, not the pre-claim snapshot: the
                # caller must be able to see the lease it now holds.
                item["status"] = "running"
                item["attempts"] = int(row["attempts"]) + 1
                item["lease_owner"] = owner
                item["lease_expires_at"] = expires
                item["started_at"] = now
                item["payload"] = _loads_json(row["payload_json"], {})
                claimed.append(item)
            return claimed

    def recover_stale_jobs(self, *, stale_after_sec: int = 900) -> int:
        """Re-queue running jobs whose lease lapsed; return how many recovered.

        This is what makes the queue crash-safe: a worker that died mid-job
        leaves a ``running`` row behind, and any later worker (or a plain CLI
        invocation) sweeps it back to ``pending`` once the lease expires.
        """
        with self._connection() as conn:
            return self._recover_stale(conn, _now(), stale_after_sec)

    @staticmethod
    def _recover_stale(conn: sqlite3.Connection, now: str, stale_after_sec: int) -> int:
        cursor = conn.execute(
            "UPDATE memory_jobs SET status='pending', lease_owner='', lease_expires_at='', "
            "started_at='' WHERE status='running' AND ("
            "(lease_expires_at <> '' AND lease_expires_at <= ?) OR "
            "(lease_expires_at = '' AND started_at <> '' "
            "AND (julianday(?) - julianday(started_at))*86400 > ?))",
            (now, now, max(1, stale_after_sec)),
        )
        return max(0, int(cursor.rowcount))

    def complete_job(self, job_id: int) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE memory_jobs SET status='completed', completed_at=?, last_error='', "
                "lease_owner='', lease_expires_at='' WHERE job_id=?",
                (_now(), int(job_id)),
            )

    def fail_job(
        self,
        job_id: int,
        error: str,
        *,
        retry: bool = True,
        max_attempts: int = DEFAULT_JOB_MAX_ATTEMPTS,
    ) -> bool:
        """Mark a job failed, re-queuing it until the attempt budget is spent.

        Returns ``True`` when the job was re-queued for another attempt and
        ``False`` when it reached the terminal ``failed`` state (budget exhausted
        or ``retry=False``), so the worker can log/escalate accordingly.
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT attempts FROM memory_jobs WHERE job_id=?", (int(job_id),)
            ).fetchone()
            attempts = int(row["attempts"]) if row is not None else 0
            exhausted = not retry or attempts >= max(1, int(max_attempts))
            conn.execute(
                "UPDATE memory_jobs SET status=?, last_error=?, lease_owner='', "
                "lease_expires_at='', completed_at=? WHERE job_id=?",
                (
                    "failed" if exhausted else "pending",
                    str(error)[:1000],
                    _now() if exhausted else "",
                    int(job_id),
                ),
            )
        return not exhausted

    def record_generation(self, *, layer: str, project_id: str, status: str = 'completed',
                          source_run_id: str = '', source_seq_start: int = 0,
                          source_seq_end: int = 0, prompt: str = '', input_text: str = '',
                          output_text: str = '', model: str = '', model_config_hash: str = '',
                          record_ids: list[int] | None = None, error: str = '') -> int:
        def digest(value: str) -> str:
            return hashlib.sha256(value.encode('utf-8')).hexdigest() if value else ''
        now = _now()
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO memory_generations (layer, project_id, source_run_id, source_seq_start, "
                "source_seq_end, prompt_hash, input_hash, output_hash, model, model_config_hash, status, "
                "record_ids_json, created_at, completed_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (layer, project_id, source_run_id, int(source_seq_start), int(source_seq_end), digest(prompt),
                 digest(input_text), digest(output_text), model, model_config_hash, status,
                 json.dumps(record_ids or []), now, now if status in {'completed', 'failed'} else '', error[:1000]),
            )
            generation_id = cursor.lastrowid
            assert generation_id is not None
            return int(generation_id)

    def publish_snapshot(self, *, project_id: str, generation: int = 0) -> int:
        """Publish the frozen L2/L3 read view in one transaction.

        The snapshot is the *stable* view behind the cached prompt prefix: L2/L3
        are low-frequency and ride the system track, while L1 stays dynamically
        recalled per goal — so only L2/L3 items are frozen here.  A concurrent
        background projection publishes a new snapshot; the running prompt keeps
        its pinned snapshot id until the next compaction epoch.
        """
        with self._connection() as conn:
            rows = {
                'l2': conn.execute("SELECT scenario_id FROM scenarios WHERE status='active' ORDER BY updated_at DESC").fetchall(),
                'l3': conn.execute("SELECT persona_id FROM persona WHERE state='confirmed' ORDER BY origin DESC, updated_at DESC").fetchall(),
            }
            ids = [f"{layer}:{int(row[0])}" for layer, values in rows.items() for row in values]
            content_hash = hashlib.sha256('|'.join(ids).encode('utf-8')).hexdigest()
            conn.execute("UPDATE memory_snapshots SET status='superseded' WHERE project_id=? AND status='published'", (project_id,))
            cursor = conn.execute(
                "INSERT INTO memory_snapshots (project_id, generation, content_hash, status, created_at, published_at) VALUES (?, ?, ?, 'published', ?, ?)",
                (project_id, int(generation), content_hash, _now(), _now()),
            )
            raw_snapshot_id = cursor.lastrowid
            assert raw_snapshot_id is not None
            snapshot_id = int(raw_snapshot_id)
            for layer, values in rows.items():
                for rank, row in enumerate(values):
                    conn.execute(
                        "INSERT INTO memory_snapshot_items (snapshot_id, layer, item_id, rank) VALUES (?, ?, ?, ?)",
                        (snapshot_id, layer, int(row[0]), rank),
                    )
            return snapshot_id

    def active_snapshot_id(self, *, project_id: str) -> int | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT snapshot_id FROM memory_snapshots WHERE project_id=? AND status='published' ORDER BY snapshot_id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return int(row['snapshot_id']) if row else None

    def reset_derived(self) -> None:
        """Clear rebuildable read models while leaving the EventLog untouched."""
        with self._connection() as conn:
            for table in (
                "memory_snapshot_items",
                "memory_snapshots",
                "memory_generations",
                "memory_jobs",
                "scenario_members",
                "memories_fts",
                "memories",
                "scenarios",
                "persona",
            ):
                conn.execute(f"DELETE FROM {table}")

    def repair_derived(self, *, embedder: Embedder | None = None) -> dict[str, int]:
        """Deterministically repair the L1 read model's indexes — no model calls.

        Backfills missing ``topic_key``s from provenance, rebuilds the FTS5
        index from stored content, and (when an embedder is given) re-embeds
        rows whose vector is NULL.  Rows are updated in place; the EventLog is
        never touched.  This is the ``deterministic`` rebuild track (spec §10).
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT record_id, content, type, topic_key, source_json, metadata_json "
                "FROM memories ORDER BY record_id"
            ).fetchall()
            backfilled = 0
            for row in rows:
                if str(row["topic_key"] or ""):
                    continue
                topic_key = derive_topic_key(
                    _loads_json(row["source_json"], {}),
                    _loads_json(row["metadata_json"], {}),
                )
                if topic_key:
                    conn.execute(
                        "UPDATE memories SET topic_key=? WHERE record_id=?",
                        (topic_key, int(row["record_id"])),
                    )
                    backfilled += 1
            # The FTS index is a pure function of (record_id, content, type).
            conn.execute("DELETE FROM memories_fts")
            for row in rows:
                conn.execute(
                    "INSERT INTO memories_fts (record_id, content, type) VALUES (?, ?, ?)",
                    (int(row["record_id"]), row["content"], row["type"]),
                )
        embeddings_added = 0
        if embedder is not None:
            with self._connection() as conn:
                missing = conn.execute(
                    "SELECT record_id, content FROM memories WHERE embedding IS NULL"
                ).fetchall()
            for row in missing:
                try:
                    vector = _embedding_bytes(embedder(row["content"]))
                except Exception:  # noqa: BLE001 — one bad vector must not fail the pass
                    continue
                with self._connection() as conn:
                    conn.execute(
                        "UPDATE memories SET embedding=? WHERE record_id=?",
                        (vector, int(row["record_id"])),
                    )
                embeddings_added += 1
        return {
            "memories": len(rows),
            "topic_keys_backfilled": backfilled,
            "fts_rows": len(rows),
            "embeddings_added": embeddings_added,
        }

    def list_snapshot_scenarios(self, snapshot_id: int, *, limit: int | None = None) -> list[ScenarioEntry]:
        sql = (
            "SELECT s.* "
            "FROM memory_snapshot_items i JOIN scenarios s ON s.scenario_id=i.item_id "
            "WHERE i.snapshot_id=? AND i.layer='l2' ORDER BY i.rank"
        )
        params: list[Any] = [int(snapshot_id)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ScenarioEntry.from_row(row) for row in rows]

    def list_snapshot_persona(self, snapshot_id: int) -> list[PersonaEntry]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT p.* "
                "FROM memory_snapshot_items i JOIN persona p ON p.persona_id=i.item_id "
                "WHERE i.snapshot_id=? AND i.layer='l3' ORDER BY i.rank",
                (int(snapshot_id),),
            ).fetchall()
        return [PersonaEntry.from_row(row) for row in rows]

    def add_memories(self, memories: list[L1Memory]) -> list[int]:
        """Persist a batch of memories, returning their assigned record ids.

        ``topic_key`` is computed once here and stored — L2 clustering reads the
        column instead of re-deriving keys on every pass.  When an embedder is
        injected, each memory's content is embedded and held in the
        ``embedding`` BLOB column for hybrid (RRF) recall; an embedder failure
        degrades that one row to ``NULL`` rather than failing the batch.
        """
        if not memories:
            return []
        ids: list[int] = []
        with self._connection() as conn:
            for memory in memories:
                now = memory.created_at or _now()
                embedding: bytes | None = None
                if self.embedder is not None:
                    try:
                        embedding = _embedding_bytes(self.embedder(memory.content))
                    except Exception:  # noqa: BLE001 — one bad vector must not fail the row
                        embedding = None
                topic_key = memory.topic_key or derive_topic_key(memory.source, memory.metadata)
                source_run_id = memory.source_run_id or str(memory.source.get("run_id") or "")
                cursor = conn.execute(
                    "INSERT INTO memories "
                    "(type, content, priority, scope, session_id, topic_key, confidence, "
                    " status, source_run_id, source_json, created_at, updated_at, "
                    " metadata_json, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        memory.type,
                        memory.content,
                        _clamp_priority(memory.priority),
                        memory.scope,
                        memory.session_id,
                        topic_key,
                        _clamp_confidence(memory.confidence),
                        memory.status or "active",
                        source_run_id,
                        json.dumps(memory.source, ensure_ascii=False),
                        now,
                        now,
                        json.dumps(memory.metadata, ensure_ascii=False),
                        embedding,
                    ),
                )
                record_id = cursor.lastrowid
                assert record_id is not None
                conn.execute(
                    "INSERT INTO memories_fts (record_id, content, type) VALUES (?, ?, ?)",
                    (record_id, memory.content, memory.type),
                )
                ids.append(int(record_id))
        return ids

    def search(
        self,
        query: str,
        *,
        scope: MemoryScope = "project",
        session_id: str | None = None,
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        memories, _ = self.search_with_diagnostics(
            query, scope=scope, session_id=session_id, limit=limit
        )
        return memories

    def search_with_diagnostics(
        self,
        query: str,
        *,
        scope: MemoryScope = "project",
        session_id: str | None = None,
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> tuple[list[L1Memory], dict[str, Any]]:
        """Recall with automatic strategy plus retrieval diagnostics (plan §4.4).

        Pure BM25 when no embedder is injected; BM25 + embedding RRF when one
        is, so the caller never picks the strategy and a missing embedder (or a
        vector outage) degrades to keyword recall for free.  The diagnostics
        dict mirrors the ``memory/recall`` audit contract: candidate ids per
        strategy, the fused RRF ranking, and the final (reranked, budget-trimmed)
        record ids.
        """
        diagnostics: dict[str, Any] = {
            "retrieval_mode": "bm25",
            "bm25_candidates": [],
            "vector_candidates": [],
            "rrf_rank": {},
            "final_record_ids": [],
        }
        fts_query = _fts_query(query)
        if not fts_query:
            return [], diagnostics
        bm25 = self._bm25_search(
            query, scope=scope, session_id=session_id, limit=RECALL_CANDIDATE_POOL
        )
        diagnostics["bm25_candidates"] = [
            memory.record_id for memory in bm25 if memory.record_id is not None
        ]
        fused = bm25
        if self.embedder is not None:
            vector: list[L1Memory] = []
            try:
                vector = self._embedding_search(
                    self.embedder(query),
                    scope=scope,
                    session_id=session_id,
                    limit=RECALL_CANDIDATE_POOL,
                )
                diagnostics["retrieval_mode"] = "hybrid"
            except Exception:  # noqa: BLE001 — a vector outage must not fail recall
                diagnostics["retrieval_mode"] = "bm25_fallback"
            diagnostics["vector_candidates"] = [
                memory.record_id for memory in vector if memory.record_id is not None
            ]
            fused = rrf_fuse([bm25, vector], limit=RECALL_CANDIDATE_POOL)
        diagnostics["rrf_rank"] = {
            str(memory.record_id): rank
            for rank, memory in enumerate(fused, start=1)
            if memory.record_id is not None
        }
        final = _rerank(fused)[: max(0, limit)]
        diagnostics["final_record_ids"] = [
            memory.record_id for memory in final if memory.record_id is not None
        ]
        return final, diagnostics

    def _bm25_search(
        self,
        query: str,
        *,
        scope: MemoryScope = "project",
        session_id: str | None = None,
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        with self._connection() as conn:
            session_clause = "AND m.session_id = ? " if session_id is not None else ""
            sql = (
                "SELECT m.* "
                "FROM memories_fts "
                "JOIN memories m ON m.record_id = memories_fts.record_id "
                "WHERE memories_fts MATCH ? AND m.scope = ? "
                + session_clause
                + "ORDER BY bm25(memories_fts) ASC, m.priority DESC LIMIT ?"
            )
            rows = conn.execute(
                sql,
                (
                    (fts_query, scope, session_id, max(0, limit))
                    if session_id is not None
                    else (fts_query, scope, max(0, limit))
                ),
            ).fetchall()
        return [L1Memory.from_row(row) for row in rows]

    def _embedding_search(
        self,
        query_vec: list[float],
        *,
        scope: MemoryScope = "project",
        session_id: str | None = None,
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        with self._connection() as conn:
            where = "scope = ?"
            params: list[Any] = [scope]
            if session_id is not None:
                where += " AND session_id = ?"
                params.append(session_id)
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where}",
                params,
            ).fetchall()
        scored: list[tuple[float, L1Memory]] = []
        for row in rows:
            vector = _loads_embedding(row["embedding"])
            if vector is None:
                continue
            memory = L1Memory.from_row(row)
            scored.append((_cosine(query_vec, vector), memory))
        scored.sort(key=lambda pair: (-pair[0], pair[1].priority))
        return [memory for _, memory in scored[:limit]]

    def update_memory(
        self,
        record_id: int,
        content: str,
        *,
        memory: L1Memory | None = None,
        merge: bool = False,
    ) -> bool:
        """Replace an existing memory's content (used by dedup update/merge).

        Returns ``False`` when the record is gone; never raises.  On merge, all
        prior provenance keys are preserved (conflicting values become lists) and
        confidence/priority only ratchet upward.  The FTS row and the persisted
        ``topic_key`` are kept in step with the merged content/provenance.
        """
        now = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT record_id FROM memories WHERE record_id = ?", (record_id,)
            ).fetchone()
            if existing is None:
                return False
            if memory is None:
                conn.execute(
                    "UPDATE memories SET content = ?, updated_at = ? WHERE record_id = ?",
                    (content, now, record_id),
                )
            else:
                row = conn.execute(
                    "SELECT source_json, metadata_json, priority, confidence FROM memories WHERE record_id = ?",
                    (record_id,),
                ).fetchone()
                old_source = _loads_json(row["source_json"], {}) if row else {}
                old_meta = _loads_json(row["metadata_json"], {}) if row else {}
                source = dict(old_source)
                for key, value in memory.source.items():
                    if key not in source:
                        source[key] = value
                    elif source[key] != value and merge:
                        prior = source[key] if isinstance(source[key], list) else [source[key]]
                        incoming = value if isinstance(value, list) else [value]
                        source[key] = list(dict.fromkeys(prior + incoming))
                metadata = dict(old_meta)
                metadata.update(memory.metadata)
                old_confidence = float(row["confidence"]) if row and row["confidence"] is not None else 0.5
                topic_key = derive_topic_key(source, metadata)
                conn.execute(
                    "UPDATE memories SET content=?, priority=?, topic_key=?, confidence=?, "
                    "source_json=?, metadata_json=?, updated_at=? WHERE record_id=?",
                    (content,
                     max(int(row["priority"] if row else 0), _clamp_priority(memory.priority)),
                     topic_key,
                     max(old_confidence, _clamp_confidence(memory.confidence)),
                     json.dumps(source, ensure_ascii=False),
                     json.dumps(metadata, ensure_ascii=False),
                     now, record_id),
                )
            conn.execute(
                "UPDATE memories_fts SET content = ? WHERE record_id = ?",
                (content, record_id),
            )
        return True

    def get_memory(self, record_id: int) -> L1Memory | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE record_id = ?", (int(record_id),)
            ).fetchone()
        return L1Memory.from_row(row) if row is not None else None

    def supersede_record(self, old_record_id: int, new_record_id: int) -> bool:
        """Mark an active record superseded by a newer one (dedup topic change).

        Superseded rows stay queryable for provenance but drop out of recall and
        L2 clustering; the touched topic is expected to re-synthesize.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE memories SET status = 'superseded', supersedes_record_id = ?, "
                "updated_at = ? WHERE record_id = ? AND status = 'active'",
                (int(new_record_id), _now(), int(old_record_id)),
            )
            return cursor.rowcount > 0

    def active_memories_by_topic(
        self,
        topic_key: str,
        *,
        types: frozenset[str] | set[str] | None = None,
        limit: int = 20,
    ) -> list[L1Memory]:
        """Active project-scoped memories for one persisted topic key."""
        if not topic_key:
            return []
        clauses = ["scope = 'project'", "status = 'active'", "topic_key = ?"]
        params: list[Any] = [topic_key]
        if types:
            clauses.append(f"type IN ({', '.join('?' * len(types))})")
            params.extend(sorted(types))
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memories "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (*params, max(0, limit)),
            ).fetchall()
        return [L1Memory.from_row(row) for row in rows]

    def topic_cluster_stats(
        self,
        *,
        types: frozenset[str] | set[str] | None = None,
        min_facts: int = 1,
        min_runs: int = 1,
    ) -> dict[str, dict[str, int]]:
        """Qualifying topic clusters: enough facts, from enough distinct runs.

        The run-count guard keeps one turn's repeated output from conjuring a
        scenario — a topic only escalates once several independent runs agree.
        """
        clauses = ["scope = 'project'", "status = 'active'", "topic_key <> ''"]
        params: list[Any] = []
        if types:
            clauses.append(f"type IN ({', '.join('?' * len(types))})")
            params.extend(sorted(types))
        clauses.append("1 = 1 GROUP BY topic_key")
        having = "HAVING COUNT(*) >= ? AND COUNT(DISTINCT source_run_id) >= ?"
        params.extend([max(1, min_facts), max(1, min_runs)])
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT topic_key, COUNT(*) AS fact_count, "
                "COUNT(DISTINCT source_run_id) AS run_count "
                f"FROM memories WHERE {' AND '.join(clauses)} {having}",
                params,
            ).fetchall()
        return {
            str(row["topic_key"]): {
                "fact_count": int(row["fact_count"]),
                "run_count": int(row["run_count"]),
            }
            for row in rows
        }

    def list_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        session_id: str | None = None,
    ) -> list[L1Memory]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories {where} ORDER BY record_id",
                params,
            ).fetchall()
        return [L1Memory.from_row(row) for row in rows]

    def count_memories(self, *, scope: MemoryScope | None = None) -> int:
        where = "WHERE scope = ?" if scope is not None else ""
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM memories {where}",
                (scope,) if scope is not None else (),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def persona_by_rule_key(self, rule_key: str, *, origin: str = "auto") -> PersonaEntry | None:
        if not rule_key:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM persona WHERE rule_key = ? AND origin = ?",
                (rule_key, origin),
            ).fetchone()
        return PersonaEntry.from_row(row) if row is not None else None

    def upsert_persona(self, entry: PersonaEntry) -> int:
        """Insert or update one L3 rule row, keyed by ``rule_key`` + ``origin``.

        Re-synthesis of the same rule updates its row in place (text, source,
        confidence, state machine counters) instead of piling up rows; the
        single-auto-row replace of the v1 schema is gone because rules are now
        independent rows.  Manual entries are still never persisted — they are
        materialized from feedback rules at injection time — but the method
        accepts them for completeness.
        """
        now = entry.updated_at or _now()
        source_ids = json.dumps(entry.source_record_ids, ensure_ascii=False)
        with self._connection() as conn:
            if entry.origin == "auto" and entry.rule_key:
                existing = conn.execute(
                    "SELECT persona_id, confirmation_count, confirmed_at FROM persona "
                    "WHERE rule_key = ? AND origin = 'auto'",
                    (entry.rule_key,),
                ).fetchone()
            else:
                existing = None
            if existing is not None:
                persona_id = int(existing["persona_id"])
                confirmed_at = entry.confirmed_at or str(existing["confirmed_at"] or "")
                conn.execute(
                    "UPDATE persona SET profile = ?, style = ?, hard_rule = ?, "
                    " source_record_ids = ?, confidence = ?, state = ?, "
                    " confirmation_count = ?, confirmed_at = ?, generation_id = ?, "
                    " updated_at = ? WHERE persona_id = ?",
                    (
                        entry.profile,
                        entry.style,
                        entry.hard_rule,
                        source_ids,
                        entry.confidence,
                        entry.state,
                        max(int(existing["confirmation_count"]), entry.confirmation_count),
                        confirmed_at,
                        entry.generation_id,
                        now,
                        persona_id,
                    ),
                )
                return persona_id
            cursor = conn.execute(
                "INSERT INTO persona (rule_key, profile, style, hard_rule, source_record_ids, "
                " origin, confidence, state, confirmation_count, confirmed_at, generation_id, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.rule_key,
                    entry.profile,
                    entry.style,
                    entry.hard_rule,
                    source_ids,
                    entry.origin,
                    entry.confidence,
                    entry.state,
                    entry.confirmation_count,
                    entry.confirmed_at,
                    entry.generation_id,
                    now,
                    now,
                ),
            )
            inserted_id = cursor.lastrowid
            assert inserted_id is not None
            return int(inserted_id)

    def set_persona_state(self, persona_id: int, state: str) -> bool:
        """Move one L3 rule along the candidate/confirmed/rejected lifecycle."""
        if state not in {"candidate", "confirmed", "rejected", "superseded"}:
            return False
        confirmed_at = _now() if state == "confirmed" else ""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE persona SET state = ?, confirmed_at = CASE WHEN ? = 'confirmed' THEN ? ELSE confirmed_at END, "
                "updated_at = ? WHERE persona_id = ?",
                (state, state, confirmed_at, _now(), int(persona_id)),
            )
            return cursor.rowcount > 0

    def reject_persona(self, rule_key: str, *, origin: str = "auto") -> bool:
        """Explicitly reject an auto rule candidate (lifecycle ``rejected``)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE persona SET state = 'rejected', updated_at = ? "
                "WHERE rule_key = ? AND origin = ?",
                (_now(), rule_key, origin),
            )
            return cursor.rowcount > 0

    def list_persona(
        self,
        *,
        origin: str | None = None,
        state: str | None = None,
    ) -> list[PersonaEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if origin is not None:
            clauses.append("origin = ?")
            params.append(origin)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM persona {where} ORDER BY persona_id",
                params,
            ).fetchall()
        return [PersonaEntry.from_row(row) for row in rows]

    def upsert_scenario(self, entry: ScenarioEntry, *, member_record_ids: list[int] | None = None) -> int:
        """Insert or update an L2 scenario, keyed by its persisted ``topic_key``.

        Re-synthesis of the same topic updates in place rather than piling up
        (plan §3.1), so an obsolete ``recipe`` is overwritten by the newer one.
        ``member_record_ids`` (when given) rewrites the ``scenario_members``
        relation — the authoritative L1↔L2 membership; the JSON
        ``source_record_ids`` column is only a redundant read-side copy.
        """
        now = entry.updated_at or _now()
        source_ids = json.dumps(entry.source_record_ids, ensure_ascii=False)
        topic_key = entry.topic_key or entry.scenario.strip().lower()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT scenario_id, created_at FROM scenarios WHERE topic_key = ?",
                (topic_key,),
            ).fetchone()
            if existing is not None:
                scenario_id = int(existing["scenario_id"])
                conn.execute(
                    "UPDATE scenarios SET scenario = ?, summary = ?, recipe = ?, "
                    " source_record_ids = ?, doc_ref = ?, confidence = ?, status = ?, "
                    " generation_id = ?, updated_at = ? WHERE scenario_id = ?",
                    (
                        entry.scenario,
                        entry.summary,
                        entry.recipe,
                        source_ids,
                        entry.doc_ref,
                        entry.confidence,
                        entry.status or "active",
                        entry.generation_id,
                        now,
                        scenario_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO scenarios (topic_key, scenario, summary, recipe, "
                    " source_record_ids, doc_ref, confidence, status, generation_id, "
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        topic_key,
                        entry.scenario,
                        entry.summary,
                        entry.recipe,
                        source_ids,
                        entry.doc_ref,
                        entry.confidence,
                        entry.status or "active",
                        entry.generation_id,
                        now,
                        now,
                    ),
                )
                inserted_scenario_id = cursor.lastrowid
                assert inserted_scenario_id is not None
                scenario_id = int(inserted_scenario_id)
            if member_record_ids is not None:
                conn.execute(
                    "DELETE FROM scenario_members WHERE scenario_id = ?", (scenario_id,)
                )
                for rank, record_id in enumerate(member_record_ids):
                    conn.execute(
                        "INSERT OR IGNORE INTO scenario_members "
                        "(scenario_id, record_id, contribution, rank) VALUES (?, ?, '', ?)",
                        (scenario_id, int(record_id), rank),
                    )
            return scenario_id

    def scenario_by_topic(self, topic_key: str) -> ScenarioEntry | None:
        if not topic_key:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenarios WHERE topic_key = ?", (topic_key,)
            ).fetchone()
        return ScenarioEntry.from_row(row) if row is not None else None

    def scenario_member_ids(self, scenario_id: int) -> list[int]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT record_id FROM scenario_members WHERE scenario_id = ? ORDER BY rank",
                (int(scenario_id),),
            ).fetchall()
        return [int(row["record_id"]) for row in rows]

    def list_scenarios(self, *, limit: int | None = None) -> list[ScenarioEntry]:
        sql = "SELECT * FROM scenarios ORDER BY scenario_id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, limit),)
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ScenarioEntry.from_row(row) for row in rows]


def _clamp_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, priority))


def _clamp_confidence(value: Any, *, default: float = 0.5) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(confidence):
        return default
    return max(0.0, min(1.0, confidence))


def _normalize_topic(value: Any) -> str:
    """Normalize one topic candidate into a stable key fragment.

    Lowercase, forward slashes, whitespace removed, bounded length.  Non-string
    or empty inputs normalize to ``""`` so the caller can fall through.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip().replace("\\", "/").lower()
    text = re.sub(r"\s+", "", text)
    return text[:DEFAULT_TOPIC_MAX_LENGTH]


def derive_topic_key(
    source: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
    *,
    max_length: int = DEFAULT_TOPIC_MAX_LENGTH,
) -> str:
    """Derive the persisted L2 clustering key from one memory's provenance.

    Priority: ``source.topic`` > ``source.module`` > ``source.file`` +
    ``source.symbol`` > ``source.file`` > ``source.test_name`` >
    ``metadata.topic``.  Returns ``""`` when no reliable project anchor exists —
    memories without an anchor are deliberately not clustered into scenarios.
    """
    source = source if isinstance(source, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    file_value = _normalize_topic(source.get("file"))
    symbol_value = _normalize_topic(source.get("symbol"))
    candidates = (
        source.get("topic"),
        source.get("module"),
        f"{file_value}:{symbol_value}" if file_value and symbol_value else None,
        file_value or None,
        source.get("test_name"),
        metadata.get("topic"),
    )
    for candidate in candidates:
        key = _normalize_topic(candidate)
        if key:
            return key[:max_length]
    return ""


def _ensure_column(conn: sqlite3.Connection, table: str, column_decl: str) -> None:
    column_name = column_decl.split()[0]
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_decl}")


def _fts_query(text: str, *, max_tokens: int = 8) -> str:
    """Build a bounded FTS5 MATCH query (OR of quoted word tokens).

    Returns ``""`` when the input yields no searchable tokens, so callers skip
    rather than hand FTS5 an empty/unsafe query string.  ``\\w+`` matches ASCII
    words and (Unicode-aware) CJK runs; each token is quoted so FTS5 treats it
    as a phrase and never as raw operators.
    """
    tokens = re.findall(r"\w+", text or "", re.UNICODE)
    tokens = [token for token in tokens if token][:max_tokens]
    if not tokens:
        return ""
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens]
    return " OR ".join(quoted)


def rrf_fuse(
    ranked_lists: list[list[L1Memory]],
    *,
    k: int = 60,
    limit: int = DEFAULT_MAX_RESULTS,
) -> list[L1Memory]:
    """Reciprocal Rank Fusion over one-or-more ranked recall lists.

    Each list is expected most-relevant-first; a memory's score is the sum of
    ``1 / (k + rank)`` across lists, so a hit near the top of several lists wins
    (plan §4.4: BM25 + embedding 加权 RRF).  Deterministic: no randomness.
    """
    scores: dict[int, float] = {}
    order: dict[int, L1Memory] = {}
    for ranked in ranked_lists:
        for rank, memory in enumerate(ranked, start=1):
            if memory.record_id is None:
                continue
            order[memory.record_id] = memory
            scores[memory.record_id] = scores.get(memory.record_id, 0.0) + 1.0 / (k + rank)
    ranked_ids = sorted(scores, key=lambda record_id: -scores[record_id])
    return [order[record_id] for record_id in ranked_ids[:limit]]


def rerank_score(memory: L1Memory, *, now: datetime | None = None) -> float:
    """Additive rerank boosts/penalties applied after RRF fusion.

    ``priority`` and ``confidence`` contribute small positive boosts; memories
    updated within the recency window get a boost and stale ``todo`` items get a
    penalty, so a week-old chore does not outrank fresh knowledge on a fused
    tie.  Deterministic: same inputs, same score.
    """
    score = (_clamp_priority(memory.priority) / 100.0) * RERANK_PRIORITY_WEIGHT
    score += _clamp_confidence(memory.confidence) * RERANK_CONFIDENCE_WEIGHT
    stamp = _parse_timestamp(memory.updated_at or memory.created_at)
    if stamp is not None:
        reference = now or datetime.now().astimezone()
        age_days = max(0.0, (reference - stamp).total_seconds() / 86_400.0)
        if age_days <= RERANK_RECENCY_DAYS:
            score += RERANK_RECENCY_BOOST
        if memory.type == "todo" and age_days > RERANK_STALE_TODO_DAYS:
            score -= RERANK_STALE_PENALTY
    return score


def _rerank(memories: list[L1Memory], *, now: datetime | None = None) -> list[L1Memory]:
    return sorted(memories, key=lambda memory: -rerank_score(memory, now=now))


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embedding_bytes(vector: list[float]) -> bytes:
    return json.dumps(vector).encode("utf-8")


def _loads_embedding(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [float(x) for x in data]


@dataclass(frozen=True)
class DistillResult:
    ok: bool
    memories: list[L1Memory] = field(default_factory=list)
    error_code: str | None = None
    usage: ModelUsage | None = None


class L1Distiller:
    """Distill one committed turn into L1 atomic memories via one LLM call.

    Never raises: :meth:`distill` wraps the provider call and JSON parse, and
    any failure collapses into ``DistillResult(ok=False, error_code=...)`` so
    the caller records ``memory_distill_failed`` and keeps the turn (plan §4.5).
    """

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def distill(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        run_id: str,
        session_id: str,
        run_facts: str = "",
        event_range: tuple[int, int] | None = None,
    ) -> DistillResult:
        prompt = _distill_prompt(
            user_message=user_message,
            assistant_reply=assistant_reply,
            run_id=run_id,
            run_facts=run_facts,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract durable knowledge from one coding-agent turn. "
                    "Return ONLY a JSON array, no prose. Empty array [] is a valid answer."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            response = self.provider.complete(
                messages,
                options=CompletionOptions(json_mode=True, max_tokens=None),
            )
        except (ProviderError, RuntimeError):
            return DistillResult(ok=False, error_code="provider", usage=None)
        return self._parse(
            response.text,
            session_id=session_id,
            run_id=run_id,
            usage=response.usage,
            event_range=event_range,
        )

    def _parse(
        self,
        text: str,
        *,
        session_id: str,
        run_id: str,
        usage: ModelUsage | None,
        event_range: tuple[int, int] | None = None,
    ) -> DistillResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return DistillResult(ok=False, error_code="bad_json", usage=usage)
        if not isinstance(data, list):
            return DistillResult(ok=False, error_code="bad_shape", usage=usage)
        memories: list[L1Memory] = []
        for item in data:
            memory = _coerce_memory(
                item, session_id=session_id, run_id=run_id, event_range=event_range
            )
            if memory is not None:
                memories.append(memory)
        return DistillResult(ok=True, memories=memories, usage=usage)


def _coerce_memory(
    item: Any,
    *,
    session_id: str,
    run_id: str = "",
    event_range: tuple[int, int] | None = None,
) -> L1Memory | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    content = content.strip()
    if len(content) > MAX_CONTENT_CHARS:
        return None  # oversized output is dropped, not truncated into a half-fact
    raw_type = item.get("type")
    if raw_type not in MEMORY_TYPES:
        return None
    raw_scope = item.get("scope", "project")
    if raw_scope not in MEMORY_SCOPES:
        raw_scope = "project"
    source = item.get("source")
    if not isinstance(source, dict):
        source = {}
    source = {str(key): value for key, value in source.items()}
    # Preserve the model's compact file/line source shape for compatibility;
    # the harness always stamps the authoritative run id and the turn's event
    # range, so every memory keeps an auditable L0 anchor.
    if run_id:
        source.setdefault("run_id", run_id)
    if event_range is not None:
        source.setdefault("event_seq_start", int(event_range[0]))
        source.setdefault("event_seq_end", int(event_range[1]))
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {str(key): value for key, value in metadata.items()}
    if run_id:
        metadata.setdefault("run_id", run_id)
    created_at = _now()
    return L1Memory(
        type=raw_type,
        content=content,
        priority=_clamp_priority(item.get("priority", 0)),
        scope=raw_scope,
        session_id=session_id,
        topic_key=_normalize_topic(item.get("topic_key")),
        confidence=_clamp_confidence(item.get("confidence", 0.5)),
        source=source,
        metadata=metadata,
        created_at=created_at,
        updated_at=created_at,
    )


def _distill_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    run_id: str,
    run_facts: str,
) -> str:
    return "\n\n".join(
        [
            "Distill the turn below into atomic long-term memories.",
            "Emit a JSON array of objects with exactly these fields:",
            '  {"type": "fact"|"preference"|"decision"|"constraint"|"todo",',
            '   "content": "<one self-contained atomic memory>",',
            '   "priority": 0-100 (higher = more important),',
            '   "confidence": 0.0-1.0 (how sure this holds beyond this turn),',
            '   "scope": "project"|"session",',
            '   "topic_key": "<short topic slug like db/postgres, or empty>",',
            '   "source": {"run_id": "...", "file": "<path or empty>", "line": <int or null>}}',
            'Use "project" scope only for facts/preferences that hold across sessions for '
            'this project — never for something true only of this conversation; use '
            '"session" for this-session-only decisions and todos.',
            'Each content must stand alone: a retrievable statement, not dialogue.',
            'When the turn contains nothing worth remembering, return [].',
            f"run_id: {run_id}",
            (
                f"Run facts:\n{run_facts.strip()}"
                if run_facts.strip()
                else "Run facts: (none)"
            ),
            f"User message:\n{user_message.strip()}",
            f"Assistant reply:\n{assistant_reply.strip()}",
        ]
    )


@dataclass(frozen=True)
class RecallResult:
    ok: bool
    memories: list[L1Memory] = field(default_factory=list)
    error_code: str | None = None
    # Per-scope retrieval diagnostics (plan §4.4): mirrors the ``memory/recall``
    # audit contract — retrieval_mode, candidate ids per strategy, the fused RRF
    # ranking, and the final record ids, keyed by scope.
    diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)


def recall_memories(
    store: MemoryStore,
    query: str,
    *,
    scope: MemoryScope = "project",
    limit: int = DEFAULT_MAX_RESULTS,
    timeout_sec: float = DEFAULT_RECALL_TIMEOUT_SEC,
) -> RecallResult:
    """Best-effort recall; never raises (plan §4.5).

    ``timeout_sec`` is a budget note only — SQLite BM25 is synchronous and fast;
    the real guarantee here is that *any* failure collapses into an empty,
    structured :class:`RecallResult` instead of an exception.
    """
    del timeout_sec  # retained in the signature for the plan §4.4 budget contract
    if not query.strip():
        return RecallResult(ok=True, memories=[])
    try:
        memories = store.search(query, scope=scope, limit=limit)
    except Exception as exc:  # noqa: BLE001 — memory must never propagate
        return RecallResult(ok=False, error_code=f"recall_error:{type(exc).__name__}")
    return RecallResult(ok=True, memories=memories)


def recall_scoped_memories(
    store: MemoryStore,
    query: str,
    *,
    session_id: str = "",
    limit: int = DEFAULT_MAX_RESULTS,
    timeout_sec: float = DEFAULT_RECALL_TIMEOUT_SEC,
) -> RecallResult:
    """Recall session-local facts first, then project facts.

    Session memories describe the active task and are allowed to outrank older
    project knowledge. The two result sets are deduplicated by record id and
    bounded before prompt rendering. Any store failure still degrades to an
    empty result, matching :func:`recall_memories`.
    """
    del timeout_sec
    if not query.strip() or limit <= 0:
        return RecallResult(ok=True, memories=[])
    diagnostics: dict[str, dict[str, Any]] = {}
    try:
        session_rows: list[L1Memory] = []
        if session_id:
            session_rows, session_diag = store.search_with_diagnostics(
                query, scope="session", session_id=session_id, limit=limit
            )
            diagnostics["session"] = session_diag
        project_rows, project_diag = store.search_with_diagnostics(
            query, scope="project", limit=limit
        )
        diagnostics["project"] = project_diag
    except Exception as exc:  # noqa: BLE001
        return RecallResult(ok=False, error_code=f"recall_error:{type(exc).__name__}")
    result: list[L1Memory] = []
    seen: set[int] = set()
    for memory in [*session_rows, *project_rows]:
        if memory.record_id is not None and memory.record_id in seen:
            continue
        if memory.record_id is not None:
            seen.add(memory.record_id)
        result.append(memory)
        if len(result) >= limit:
            break
    return RecallResult(ok=True, memories=result, diagnostics=diagnostics)


def format_relevant_memories(
    memories: list[L1Memory],
    *,
    max_chars_per_memory: int = DEFAULT_MAX_CHARS_PER_MEMORY,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """Render a ``<relevant-memories>`` block bounded by the token budget."""
    if not memories:
        return ""
    lines: list[str] = []
    total = 0
    for memory in memories:
        content = memory.content
        if len(content) > max_chars_per_memory:
            content = content[: max(max_chars_per_memory - 3, 0)] + "..."
        entry = f"- [{memory.type}] ({memory.scope}, p{memory.priority}) {content}"
        entry_chars = len(entry)
        if lines and total + entry_chars > max_total_chars:
            lines.append("- ... (more memories truncated)")
            break
        lines.append(entry)
        total += entry_chars
    return "<relevant-memories>\n" + "\n".join(lines) + "\n</relevant-memories>"


@dataclass(frozen=True)
class ProjectionInput:
    """Everything one L1 projection pass needs, with no live-run closures.

    The synchronous hook builds it from the live ``SessionTurnResult``; the
    background job processor builds the equivalent from a durable
    ``memory_jobs`` row plus the EventLog evidence range — same pipeline, two
    front doors (spec §6).
    """

    session_id: str
    run_id: str
    project_id: str
    user_message: str
    assistant_reply: str
    run_facts: str = ""
    event_range: tuple[int, int] | None = None
    # Carries ``metrics`` plus the ``_event_log`` provenance sink; a plain
    # namespace suffices, so background processing needs no live run state.
    state: Any = None


class MemoryProjector:
    """The shared L0→L1→(L2/L3) projection pipeline.

    One pass = distill the turn into atomic memories → record the L1
    generation → dedup-store → escalate (L2/L3) → publish the prompt snapshot.
    Both the synchronous turn-end hook and the recoverable background job
    processor delegate here, so the two modes cannot drift.
    """

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

    def project(self, projection: ProjectionInput) -> None:
        state = projection.state
        metrics = getattr(state, "metrics", None)
        if metrics is None:
            metrics = {}
        outcome = self.distiller.distill(
            user_message=projection.user_message,
            assistant_reply=projection.assistant_reply,
            run_id=projection.run_id,
            session_id=projection.session_id,
            run_facts=projection.run_facts,
            event_range=projection.event_range,
        )
        seq_start, seq_end = projection.event_range or (0, 0)
        generation_id = self.store.record_generation(
            layer="L1",
            project_id=projection.project_id,
            source_run_id=projection.run_id,
            source_seq_start=seq_start,
            source_seq_end=seq_end,
            input_text=projection.user_message + "\n" + projection.assistant_reply,
            output_text=json.dumps(
                [memory.to_dict() for memory in outcome.memories], ensure_ascii=False
            ),
            status="completed" if outcome.ok else "failed",
            error=outcome.error_code or "",
        )
        metrics["memory_generation_id"] = generation_id
        if not outcome.ok:
            metrics["memory_distill_failed"] = int(
                metrics.get("memory_distill_failed", 0)
            ) + 1
            metrics["memory_distill_error"] = outcome.error_code
            _record_memory_event(
                state,
                "memory/l1_failed",
                {"run_id": projection.run_id, "error_code": outcome.error_code},
            )
            return
        metrics["memory_distill_successes"] = int(
            metrics.get("memory_distill_successes", 0)
        ) + 1
        metrics["memory_distill_count"] = len(outcome.memories)
        if outcome.usage is not None:
            metrics["memory_distill_prompt_tokens"] = outcome.usage.prompt_tokens or 0
            metrics["memory_distill_completion_tokens"] = outcome.usage.completion_tokens or 0
        if not outcome.memories:
            _record_memory_event(
                state,
                "memory/l1_extracted",
                {"run_id": projection.run_id, "count": 0, "record_ids": []},
            )
            return
        stored = self.store_with_dedup(state, outcome.memories)
        metrics["memory_stored"] = len(stored)
        _record_memory_event(
            state,
            "memory/l1_extracted",
            {
                "run_id": projection.run_id,
                "count": len(outcome.memories),
                "stored_count": len(stored),
                "record_ids": [
                    memory.record_id for memory in stored if memory.record_id is not None
                ],
                "scopes": sorted({memory.scope for memory in outcome.memories}),
            },
        )
        if self.escalator is not None:
            # Provenance for L3 explicit-confirmation: the turn's own user
            # message is where "确认记住" lives, not the session goal.
            state._turn_user_message = projection.user_message
            self.escalator(projection.session_id, state, stored)
        try:
            snapshot_id = self.store.publish_snapshot(
                project_id=projection.project_id, generation=generation_id
            )
            metrics["memory_snapshot_published"] = snapshot_id
        except Exception:  # noqa: BLE001 — snapshot publication is best effort
            metrics["memory_snapshot_publish_failed"] = int(
                metrics.get("memory_snapshot_publish_failed", 0)
            ) + 1

    def store_with_dedup(self, state: Any, memories: list[L1Memory]) -> list[L1Memory]:
        """Store a distill batch, running LLM dedup first when a deduper is set.

        Each new memory is matched against its BM25 candidate pool and classified
        store / skip / update / merge.  On dedup failure (deduper returns ``None``
        or raises) the batch is appended unchanged (plan §4.5: 宁可冗余，不可丢).
        Returns the surviving memories with their persisted ``record_id`` (and,
        for update/merge targets, the refreshed stored row) so the escalation
        pass sees exactly which rows and topics this turn touched.
        """
        metrics = getattr(state, "metrics", None) or {}
        if self.deduper is None:
            return self.append_all(memories)

        candidates: list[L1Memory] = []
        seen: set[int] = set()
        for memory in memories:
            for candidate in self.store.search(
                memory.content, scope=memory.scope, limit=DEFAULT_DEDUP_CANDIDATES
            ):
                record_id = candidate.record_id
                if record_id is not None and record_id not in seen:
                    seen.add(record_id)
                    candidates.append(candidate)

        try:
            decisions = self.deduper.dedup(memories, candidates)
        except Exception:  # noqa: BLE001 — degrade to append-all
            decisions = None
        if decisions is None:
            metrics["memory_dedup_failed"] = int(metrics.get("memory_dedup_failed", 0)) + 1
            self._record_dedup_generation(
                state, memories=memories, candidates=candidates, decisions=None
            )
            _record_memory_event(
                state,
                "memory/l1_deduped",
                {"outcome": "failed", "fallback": "append_all"},
            )
            return self.append_all(memories)

        decision_by_index: dict[int, Any] = {}
        for decision in decisions:
            index = getattr(decision, "index", None)
            if isinstance(index, int):
                decision_by_index[index] = decision

        self._record_dedup_generation(
            state, memories=memories, candidates=candidates, decisions=decisions
        )

        _record_memory_event(
            state,
            "memory/l1_deduped",
            {
                "outcome": "ok",
                "actions": {
                    action: sum(
                        1
                        for decision in decision_by_index.values()
                        if (getattr(decision, "action", "") or "store") == action
                    )
                    for action in ("merge", "skip", "store", "update")
                },
            },
        )
        stored: list[L1Memory] = []
        for index, memory in enumerate(memories):
            decision = decision_by_index.get(index)
            if decision is None:
                _bump(metrics, "memory_dedup_store")
                stored.extend(self.append_all([memory]))
                continue
            action = getattr(decision, "action", "store") or "store"
            _bump(metrics, f"memory_dedup_{action}")
            if action == "store":
                stored.extend(self.append_all([memory]))
            elif action in {"update", "merge"}:
                record_id = getattr(decision, "record_id", None)
                content = getattr(decision, "content", None) or memory.content
                if isinstance(record_id, int) and self.store.update_memory(
                    record_id,
                    content,
                    memory=memory,
                    merge=action == "merge",
                ):
                    _record_memory_event(
                        state,
                        "memory/l1_merged" if action == "merge" else "memory/l1_updated",
                        {"record_id": record_id},
                    )
                    updated = self.store.get_memory(record_id)
                    stored.append(updated if updated is not None else memory)
                else:
                    stored.extend(self.append_all([memory]))
            # "skip" -> drop
        return stored

    def append_all(self, memories: list[L1Memory]) -> list[L1Memory]:
        """Persist a batch unchanged and stamp each memory with its record id."""
        stored: list[L1Memory] = []
        for memory, record_id in zip(
            memories, self.store.add_memories(memories), strict=False
        ):
            memory.record_id = record_id
            stored.append(memory)
        return stored

    def _record_dedup_generation(
        self,
        state: Any,
        *,
        memories: list[L1Memory],
        candidates: list[L1Memory],
        decisions: list[Any] | None,
    ) -> None:
        """Audit one dedup call (spec §7): hashes only, success and failure alike.

        Every LLM dedup invocation gets a ``DEDUP`` row in ``memory_generations``
        so a rebuild or review can see what the model was asked and answered —
        the stored text is the decision list, everything else is SHA-256.
        """
        project_id = str(
            getattr(state, "project_id", "")
            or getattr(state, "workspace_host_path", "")
            or "project"
        )
        input_text = json.dumps(
            {
                "candidates": [candidate.content for candidate in candidates],
                "new": [memory.content for memory in memories],
            },
            ensure_ascii=False,
        )
        if decisions is None:
            self.store.record_generation(
                layer="DEDUP",
                project_id=project_id,
                source_run_id=str(getattr(state, "run_id", "") or ""),
                input_text=input_text,
                status="failed",
                error="dedup_unavailable",
            )
            return
        output = [
            {
                "index": getattr(decision, "index", None),
                "action": getattr(decision, "action", "store"),
                "record_id": getattr(decision, "record_id", None),
                "content": getattr(decision, "content", None),
            }
            for decision in decisions
        ]
        self.store.record_generation(
            layer="DEDUP",
            project_id=project_id,
            source_run_id=str(getattr(state, "run_id", "") or ""),
            input_text=input_text,
            output_text=json.dumps(output, ensure_ascii=False),
            record_ids=sorted(
                {
                    decision_id
                    for decision in decisions
                    if isinstance(
                        decision_id := getattr(decision, "record_id", None), int
                    )
                }
            ),
            status="completed",
        )


class MemoryTurnHook:
    """The turn-end L1 distillation seam wired into ``SessionEngine``.

    Fired once per committed turn (plan §4.1): distill the turn into atomic
    memories, store them, then (once P1/P2 land) hand them to the escalation
    pass.  The hook never raises — ``SessionEngine`` also guards it — but it
    also records its own ``memory_distill_*`` metrics so a grand total of zero
    paths can turn a memory problem into a failed run.
    """

    def __init__(
        self,
        store: MemoryStore,
        distiller: L1Distiller,
        *,
        distill_every_n_turns: int = 1,
        escalator: Any = None,
        deduper: Any = None,
        background: bool = False,
        worker: Any = None,
    ) -> None:
        self.store = store
        self.distiller = distiller
        self.distill_every_n_turns = max(1, distill_every_n_turns)
        self.escalator = escalator
        self.deduper = deduper
        self.background = bool(background)
        self.worker = worker
        self.projector = MemoryProjector(
            store, distiller, deduper=deduper, escalator=escalator
        )
        self._turn_counter = 0

    def __call__(self, session_id: str, result: Any) -> None:
        state = getattr(result, "state", None)
        if state is None:
            return
        metrics = state.metrics
        self._turn_counter += 1
        if self._turn_counter % self.distill_every_n_turns != 0:
            return
        try:
            if self.background:
                self._enqueue(session_id, result)
            else:
                self._distill_and_store(session_id, result)
        except Exception as exc:  # noqa: BLE001 — degrade, never block the turn
            metrics["memory_distill_failed"] = int(
                metrics.get("memory_distill_failed", 0)
            ) + 1
            metrics["memory_distill_error"] = f"hook:{type(exc).__name__}"

    def _enqueue(self, session_id: str, result: Any) -> None:
        state = result.state
        event_log = getattr(state, "_event_log", None)
        seq_end = int(getattr(event_log, "last_seq", 0) or 0)
        seq_start = int(state.metrics.get("turn_start_seq", max(1, seq_end)))
        payload = {
            # The payload is a convenience cache; the authoritative input is the
            # [seq_start, seq_end] EventLog range, which any restarted worker
            # re-reads via EventEvidenceReader (spec §6: jobs truly recoverable).
            "event_log_path": str(getattr(event_log, "path", "") or ""),
            "user_message": str(getattr(result, "user_message", "") or getattr(state, "goal", "") or ""),
            "assistant_reply": str(getattr(result, "assistant_reply", "") or getattr(state, "final_answer", "") or ""),
            "run_facts": _run_facts(state),
        }
        project_id = str(getattr(state, "project_id", "") or getattr(state, "workspace_host_path", "") or "project")
        job_id = self.store.enqueue_job(
            project_id=project_id,
            session_id=session_id,
            source_run_id=str(getattr(result, "run_id", "") or getattr(state, "run_id", "")),
            source_seq_start=seq_start,
            source_seq_end=seq_end,
            payload=payload,
        )
        state.metrics["memory_job_id"] = job_id
        _record_memory_event(state, "memory/capture_requested", {"job_id": job_id, "source_seq_start": seq_start, "source_seq_end": seq_end})
        if self.worker is None:
            from minicc.memory.processor import MemoryJobProcessor
            from minicc.memory.worker import MemoryWorker

            # The processor owns no closures: it rebuilds everything from the
            # durable job row, so any worker process can finish any job.
            self.worker = MemoryWorker(
                self.store,
                MemoryJobProcessor(
                    self.store,
                    self.distiller,
                    deduper=self.deduper,
                    escalator=self.escalator,
                ),
            )
        if hasattr(self.worker, "start"):
            self.worker.start()

    def _distill_and_store(self, session_id: str, result: Any) -> None:
        """Synchronous (foreground) projection: delegate to the shared projector."""
        state = result.state
        event_log = getattr(state, "_event_log", None)
        seq_end = int(getattr(event_log, "last_seq", 0) or 0)
        self.projector.project(
            ProjectionInput(
                session_id=session_id,
                run_id=str(getattr(result, "run_id", "") or getattr(state, "run_id", "")),
                project_id=str(
                    getattr(state, "project_id", "")
                    or getattr(state, "workspace_host_path", "")
                    or "project"
                ),
                user_message=str(
                    getattr(result, "user_message", "") or getattr(state, "goal", "") or ""
                ),
                # Deliberately no ``state_summary`` fallback here: the context
                # summary is short-term compaction and never enters long-term
                # memory (spec §8: 分轨).
                assistant_reply=str(
                    getattr(result, "assistant_reply", "")
                    or getattr(state, "final_answer", "")
                    or ""
                ),
                run_facts=_run_facts(state),
                event_range=(int(state.metrics.get("turn_start_seq", max(1, seq_end))), seq_end),
                state=state,
            )
        )


def _bump(metrics: dict[str, Any], key: str) -> None:
    metrics[key] = int(metrics.get(key, 0)) + 1


def _run_facts(state: Any) -> str:
    metrics = getattr(state, "metrics", None) or {}
    fields = [
        f"status={getattr(state, 'status', '')}",
        f"turns={metrics.get('turns', 0)}",
        f"bash_actions={metrics.get('bash_actions', 0)}",
        f"command_failures={metrics.get('command_failures', 0)}",
        f"policy_denials={metrics.get('policy_denials', 0)}",
    ]
    # Feed bounded execution evidence to the distiller. This makes L1 a
    # projection of actual tool activity (commands/results/files), not merely
    # a summary of the natural-language answer.
    event_log = getattr(state, "_event_log", None)
    if event_log is not None:
        run_id = str(getattr(state, "run_id", ""))
        turn_index = int(metrics.get("turn_index", 0))
        events = [
            event
            for event in getattr(event_log, "events", [])
            if (
                event.data.get("run_id") in {None, run_id}
                and event.data.get("turn") in {None, turn_index}
            )
        ]
        evidence: list[str] = []
        for event in events[-24:]:
            data = event.data
            if event.type == "tool/call":
                evidence.append(
                    f"event#{event.seq} tool_call {data.get('tool')}: "
                    f"{_short(data.get('arguments'), 500)}"
                )
            elif event.type == "tool/result":
                evidence.append(
                    f"event#{event.seq} tool_result {data.get('tool')} "
                    f"error={data.get('is_error')}: {_short(data.get('content'), 700)}"
                )
            elif event.type == "assistant/message":
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                evidence.append(f"event#{event.seq} assistant: {_short(message.get('content'), 700)}")
        if evidence:
            fields.append("Execution evidence:\n" + "\n".join(evidence))
    return ", ".join(fields)


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(limit - 3, 0)] + "..."


def _record_memory_event(state: Any, event_type: str, data: dict[str, Any]) -> None:
    event_log = getattr(state, "_event_log", None)
    if event_log is None:
        return
    try:
        event_log.append(event_type, data)
    except Exception:
        # The memory ledger is observability; a logging failure must not affect
        # the already-committed agent turn.
        return
