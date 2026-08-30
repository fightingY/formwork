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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
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
    traceable ``{run_id, file, line}`` reference back to where the memory came
    from (plan §4.2); it is provenance, not the working-memory hash ceremony.
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "type": self.type,
            "content": self.content,
            "priority": self.priority,
            "scope": self.scope,
            "session_id": self.session_id,
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
            source=_loads_json(row["source_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_loads_json(row["metadata_json"], {}),
        )


@dataclass
class PersonaEntry:
    """One L3 persona row (``persona`` table).

    ``profile`` / ``style`` / ``hard_rule`` are the three long-term, low-frequency
    persona facets (plan §3).  ``origin`` is ``"manual"`` (human-written seed from
    feedback rules) or ``"auto"`` (synthesized from repeated L1 signals); the
    merge puts manual first so a human rule wins over an auto-distilled one.
    """

    profile: str = ""
    style: str = ""
    hard_rule: str = ""
    source_record_ids: list[int] = field(default_factory=list)
    origin: str = "auto"
    confidence: float = 0.0
    updated_at: str = ""
    persona_id: int | None = None

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
        )


@dataclass
class ScenarioEntry:
    """One L2 scenario row (``scenarios`` table).

    ``scenario`` names the topic/component; ``summary`` captures the distilled
    knowledge; ``recipe`` is the reusable fix/run path (plan §3).  ``doc_ref``
    may later point at a CLAUDE.md paragraph as the human-written seed (§3.1).
    """

    scenario: str = ""
    summary: str = ""
    recipe: str = ""
    source_record_ids: list[int] = field(default_factory=list)
    doc_ref: str = ""
    updated_at: str = ""
    scenario_id: int | None = None

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
        )


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
    scenario TEXT NOT NULL,
    summary TEXT NOT NULL,
    recipe TEXT NOT NULL DEFAULT '',
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    doc_ref TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)
"""

_PERSONA_DDL = """
CREATE TABLE IF NOT EXISTS persona (
    persona_id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '',
    hard_rule TEXT NOT NULL DEFAULT '',
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
)
"""


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
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            for ddl in (_MAIN_TABLE_DDL, _FTS_TABLE_DDL, _SCENARIOS_DDL, _PERSONA_DDL):
                conn.execute(ddl)
            conn.execute("PRAGMA user_version = 1")

    def add_memories(self, memories: list[L1Memory]) -> list[int]:
        """Persist a batch of memories, returning their assigned record ids.

        When an embedder is injected, each memory's content is embedded and held
        in the ``embedding`` BLOB column for hybrid (RRF) recall; an embedder
        failure degrades that one row to ``NULL`` rather than failing the batch.
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
                cursor = conn.execute(
                    "INSERT INTO memories "
                    "(type, content, priority, scope, session_id, source_json, "
                    " created_at, updated_at, metadata_json, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        memory.type,
                        memory.content,
                        _clamp_priority(memory.priority),
                        memory.scope,
                        memory.session_id,
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
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        """Recall with automatic strategy (plan §4.4).

        Pure BM25 when no embedder is injected; BM25 + embedding RRF when one is,
        so the caller never picks the strategy and a missing embedder degrades to
        keyword recall for free.
        """
        if self.embedder is None:
            return self._bm25_search(query, scope=scope, limit=limit)
        try:
            return self._hybrid_search(query, scope=scope, limit=limit)
        except Exception:  # noqa: BLE001 — hybrid failure degrades to BM25
            return self._bm25_search(query, scope=scope, limit=limit)

    def _bm25_search(
        self,
        query: str,
        *,
        scope: MemoryScope = "project",
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT m.record_id, m.type, m.content, m.priority, m.scope, "
                "       m.session_id, m.source_json, m.created_at, m.updated_at, "
                "       m.metadata_json "
                "FROM memories_fts "
                "JOIN memories m ON m.record_id = memories_fts.record_id "
                "WHERE memories_fts MATCH ? AND m.scope = ? "
                "ORDER BY bm25(memories_fts) ASC, m.priority DESC "
                "LIMIT ?",
                (fts_query, scope, max(0, limit)),
            ).fetchall()
        return [L1Memory.from_row(row) for row in rows]

    def _hybrid_search(
        self,
        query: str,
        *,
        scope: MemoryScope = "project",
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        assert self.embedder is not None
        bm25_results = self._bm25_search(query, scope=scope, limit=limit * 2)
        query_vec = self.embedder(query)
        embedding_results = self._embedding_search(query_vec, scope=scope, limit=limit * 2)
        return rrf_fuse([bm25_results, embedding_results], limit=limit)

    def _embedding_search(
        self,
        query_vec: list[float],
        *,
        scope: MemoryScope = "project",
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[L1Memory]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT record_id, type, content, priority, scope, session_id, "
                "       source_json, created_at, updated_at, metadata_json, embedding "
                "FROM memories WHERE scope = ?",
                (scope,),
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

    def update_memory(self, record_id: int, content: str) -> bool:
        """Replace an existing memory's content (used by dedup update/merge).

        Returns ``False`` when the record is gone; never raises.  The FTS row is
        kept in step with the content column explicitly.
        """
        now = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT record_id FROM memories WHERE record_id = ?", (record_id,)
            ).fetchone()
            if existing is None:
                return False
            conn.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE record_id = ?",
                (content, now, record_id),
            )
            conn.execute(
                "UPDATE memories_fts SET content = ? WHERE record_id = ?",
                (content, record_id),
            )
        return True

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
                "SELECT record_id, type, content, priority, scope, session_id, "
                "       source_json, created_at, updated_at, metadata_json "
                f"FROM memories {where} ORDER BY record_id",
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

    def upsert_persona(self, entry: PersonaEntry) -> int:
        """Insert or replace the L3 persona (one ``auto`` entry per project).

        An ``auto`` entry (the synthesized/updated persona) replaces the prior
        auto row, so re-synthesis updates rather than piles up (plan §3.1).
        Manual entries are never persisted — they are materialized from feedback
        rules at injection time — but the method accepts them for completeness.
        """
        now = entry.updated_at or _now()
        source_ids = json.dumps(entry.source_record_ids, ensure_ascii=False)
        with self._connection() as conn:
            if entry.origin == "auto":
                existing = conn.execute(
                    "SELECT persona_id FROM persona WHERE origin = 'auto'"
                ).fetchone()
                if existing is not None:
                    existing_id = int(existing["persona_id"])
                    conn.execute(
                        "UPDATE persona SET profile = ?, style = ?, hard_rule = ?, "
                        " source_record_ids = ?, origin = ?, confidence = ?, updated_at = ? "
                        "WHERE persona_id = ?",
                        (
                            entry.profile,
                            entry.style,
                            entry.hard_rule,
                            source_ids,
                            entry.origin,
                            entry.confidence,
                            now,
                            existing_id,
                        ),
                    )
                    return existing_id
            cursor = conn.execute(
                "INSERT INTO persona (profile, style, hard_rule, source_record_ids, "
                " origin, confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.profile,
                    entry.style,
                    entry.hard_rule,
                    source_ids,
                    entry.origin,
                    entry.confidence,
                    now,
                ),
            )
            persona_id = cursor.lastrowid
            assert persona_id is not None
            return int(persona_id)

    def list_persona(self, *, origin: str | None = None) -> list[PersonaEntry]:
        where = "WHERE origin = ?" if origin is not None else ""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT persona_id, profile, style, hard_rule, source_record_ids, "
                f"origin, confidence, updated_at FROM persona {where} ORDER BY persona_id",
                (origin,) if origin is not None else (),
            ).fetchall()
        return [PersonaEntry.from_row(row) for row in rows]

    def upsert_scenario(self, entry: ScenarioEntry) -> int:
        """Insert or update an L2 scenario, keyed by its ``scenario`` name.

        Re-synthesis of the same topic updates in place rather than piling up
        (plan §3.1), so an obsolete ``recipe`` is overwritten by the newer one.
        """
        now = entry.updated_at or _now()
        source_ids = json.dumps(entry.source_record_ids, ensure_ascii=False)
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT scenario_id FROM scenarios WHERE scenario = ?", (entry.scenario,)
            ).fetchone()
            if existing is not None:
                existing_id = int(existing["scenario_id"])
                conn.execute(
                    "UPDATE scenarios SET summary = ?, recipe = ?, source_record_ids = ?, "
                    " doc_ref = ?, updated_at = ? WHERE scenario_id = ?",
                    (
                        entry.summary,
                        entry.recipe,
                        source_ids,
                        entry.doc_ref,
                        now,
                        existing_id,
                    ),
                )
                return existing_id
            cursor = conn.execute(
                "INSERT INTO scenarios (scenario, summary, recipe, source_record_ids, "
                " doc_ref, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.scenario,
                    entry.summary,
                    entry.recipe,
                    source_ids,
                    entry.doc_ref,
                    now,
                ),
            )
            scenario_id = cursor.lastrowid
            assert scenario_id is not None
            return int(scenario_id)

    def list_scenarios(self, *, limit: int | None = None) -> list[ScenarioEntry]:
        sql = (
            "SELECT scenario_id, scenario, summary, recipe, source_record_ids, "
            "doc_ref, updated_at FROM scenarios ORDER BY scenario_id"
        )
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
        )

    def _parse(
        self,
        text: str,
        *,
        session_id: str,
        run_id: str,
        usage: ModelUsage | None,
    ) -> DistillResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return DistillResult(ok=False, error_code="bad_json", usage=usage)
        if not isinstance(data, list):
            return DistillResult(ok=False, error_code="bad_shape", usage=usage)
        memories: list[L1Memory] = []
        for item in data:
            memory = _coerce_memory(item, session_id=session_id, run_id=run_id)
            if memory is not None:
                memories.append(memory)
        return DistillResult(ok=True, memories=memories, usage=usage)


def _coerce_memory(item: Any, *, session_id: str, run_id: str = "") -> L1Memory | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
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
    # when it omits a source entirely, attach the producing run as provenance.
    if not source and run_id:
        source["run_id"] = run_id
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {str(key): value for key, value in metadata.items()}
    if run_id:
        metadata.setdefault("run_id", run_id)
    created_at = _now()
    return L1Memory(
        type=raw_type,
        content=content.strip(),
        priority=_clamp_priority(item.get("priority", 0)),
        scope=raw_scope,
        session_id=session_id,
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
            '   "scope": "project"|"session",',
            '   "source": {"run_id": "...", "file": "<path or empty>", "line": <int or null>}}',
            'Use "project" scope for facts/preferences that hold across sessions for this '
            'project; use "session" for this-session-only decisions and todos.',
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
    try:
        session_rows = (
            store.search(query, scope="session", limit=limit)
            if session_id
            else []
        )
        project_rows = store.search(query, scope="project", limit=limit)
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
    return RecallResult(ok=True, memories=result)


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
    ) -> None:
        self.store = store
        self.distiller = distiller
        self.distill_every_n_turns = max(1, distill_every_n_turns)
        self.escalator = escalator
        self.deduper = deduper
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
            self._distill_and_store(session_id, result)
        except Exception as exc:  # noqa: BLE001 — degrade, never block the turn
            metrics["memory_distill_failed"] = int(
                metrics.get("memory_distill_failed", 0)
            ) + 1
            metrics["memory_distill_error"] = f"hook:{type(exc).__name__}"

    def _distill_and_store(self, session_id: str, result: Any) -> None:
        state = result.state
        metrics = state.metrics
        user_message = str(
            getattr(result, "user_message", "") or getattr(state, "goal", "") or ""
        )
        assistant_reply = str(
            getattr(result, "assistant_reply", "")
            or getattr(state, "final_answer", "")
            or getattr(state, "state_summary", "")
            or ""
        )
        run_id = str(getattr(result, "run_id", "") or getattr(state, "run_id", ""))
        outcome = self.distiller.distill(
            user_message=user_message,
            assistant_reply=assistant_reply,
            run_id=run_id,
            session_id=session_id,
            run_facts=_run_facts(state),
        )
        if not outcome.ok:
            metrics["memory_distill_failed"] = int(
                metrics.get("memory_distill_failed", 0)
            ) + 1
            metrics["memory_distill_error"] = outcome.error_code
            _record_memory_event(
                state,
                "memory/l1_failed",
                {"run_id": run_id, "error_code": outcome.error_code},
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
                {"run_id": run_id, "count": 0, "record_ids": []},
            )
            return
        stored = self._store_with_dedup(state, outcome.memories)
        metrics["memory_stored"] = len(stored)
        _record_memory_event(
            state,
            "memory/l1_extracted",
            {
                "run_id": run_id,
                "count": len(outcome.memories),
                "stored_count": len(stored),
                "record_ids": stored,
                "scopes": sorted({memory.scope for memory in outcome.memories}),
            },
        )
        if self.escalator is not None:
            self.escalator(session_id, state, outcome.memories)

    def _store_with_dedup(self, state: Any, memories: list[L1Memory]) -> list[int]:
        """Store a distill batch, running LLM dedup first when a deduper is set.

        Each new memory is matched against its BM25 candidate pool and classified
        store / skip / update / merge.  On dedup failure (deduper returns ``None``
        or raises) the batch is appended unchanged (plan §4.5: 宁可冗余，不可丢).
        """
        metrics = getattr(state, "metrics", None) or {}
        if self.deduper is None:
            return self.store.add_memories(memories)

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
            return self.store.add_memories(memories)

        decision_by_index: dict[int, Any] = {}
        for decision in decisions:
            index = getattr(decision, "index", None)
            if isinstance(index, int):
                decision_by_index[index] = decision

        stored: list[int] = []
        for index, memory in enumerate(memories):
            decision = decision_by_index.get(index)
            if decision is None:
                _bump(metrics, "memory_dedup_store")
                stored.extend(self.store.add_memories([memory]))
                continue
            action = getattr(decision, "action", "store") or "store"
            _bump(metrics, f"memory_dedup_{action}")
            if action == "store":
                stored.extend(self.store.add_memories([memory]))
            elif action in {"update", "merge"}:
                record_id = getattr(decision, "record_id", None)
                content = getattr(decision, "content", None) or memory.content
                if isinstance(record_id, int):
                    if self.store.update_memory(record_id, content):
                        stored.append(record_id)
                    else:
                        stored.extend(self.store.add_memories([memory]))
                else:
                    stored.extend(self.store.add_memories([memory]))
            # "skip" -> drop
        return stored


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
