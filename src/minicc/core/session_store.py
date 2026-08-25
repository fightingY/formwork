"""Conversation-session persistence: ``session.json`` + ``transcript.jsonl``.

This module is the *data* layer for V5 sessions (see
``docs/V5_0_SESSION_CHAT_REMODEL_PLAN.md`` §5).  It owns nothing about how a
turn is executed -- that lives in ``core/session_engine.py``.  The transcript
is the single source of truth for conversation history; ``session.json`` is
metadata only (project root, title, turn ordering, compaction pointer).

Layout under ``<root>/<session_id>/``::

    session.json       # metadata (schema_version, project_root, turns, compaction)
    transcript.jsonl   # append-only conversation, one JSON object per line
    runs/<run_id>/     # per-turn execution evidence (reuses the existing run dirs)

The on-disk session id reuses the ``run_id`` style ``YYYYMMDD-HHMMSS-<8hex>``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

SESSION_SCHEMA_VERSION = 1
MessageRole = Literal["user", "assistant"]


class SessionNotFoundError(KeyError):
    """Raised when a session id has no persisted record."""


def new_session_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class SessionMessage:
    """One appended line of ``transcript.jsonl``."""

    seq: int
    role: MessageRole
    content: str
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "role": self.role,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMessage:
        return cls(
            seq=int(data["seq"]),
            role=data["role"],
            content=str(data.get("content", "")),
            run_id=data.get("run_id"),
        )


@dataclass
class SessionRecord:
    schema_version: int = SESSION_SCHEMA_VERSION
    session_id: str = ""
    project_root: str = ""
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    turns: list[str] = field(default_factory=list)
    compaction: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        return cls(
            schema_version=int(data.get("schema_version", SESSION_SCHEMA_VERSION)),
            session_id=str(data.get("session_id", "")),
            project_root=str(data.get("project_root", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            turns=list(data.get("turns", [])),
            compaction=dict(data.get("compaction", {})),
        )


class SessionStore:
    """Persist sessions under ``<root>/<session_id>/``.

    ``root`` defaults to ``.minicc/sessions`` relative to the current working
    directory; pass a ``tmp_path``-derived root in tests.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (Path.cwd() / ".minicc" / "sessions")

    @property
    def root(self) -> Path:
        return self._root

    def session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def session_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def transcript_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "transcript.jsonl"

    def session_runs_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "runs"

    def exists(self, session_id: str) -> bool:
        return self.session_path(session_id).exists()

    def create(self, project_root: Path | str, *, title: str = "") -> SessionRecord:
        record = SessionRecord(
            session_id=new_session_id(),
            project_root=str(Path(project_root).resolve()),
            title=title,
            created_at=_now(),
            updated_at=_now(),
        )
        self.session_dir(record.session_id).mkdir(parents=True, exist_ok=True)
        self.session_runs_dir(record.session_id).mkdir(parents=True, exist_ok=True)
        self.transcript_path(record.session_id).touch(exist_ok=True)
        self._write_record(record)
        return record

    def load(self, session_id: str) -> SessionRecord:
        path = self.session_path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"Session not found: {session_id}")
        return SessionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, record: SessionRecord) -> None:
        record.updated_at = _now()
        self._write_record(record)

    def list_sessions(self) -> list[SessionRecord]:
        if not self.root.exists():
            return []
        records: list[SessionRecord] = []
        for child in self.root.iterdir():
            if child.is_dir() and (child / "session.json").exists():
                records.append(self._read_record_file(child / "session.json"))
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records

    def rename(self, session_id: str, title: str) -> SessionRecord:
        record = self.load(session_id)
        record.title = title
        self._write_record(record)
        return record

    def append_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        *,
        run_id: str | None = None,
    ) -> SessionMessage:
        existing = self.read_transcript(session_id)
        seq = max((message.seq for message in existing), default=0) + 1
        message = SessionMessage(seq=seq, role=role, content=content, run_id=run_id)
        with self.transcript_path(session_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        return message

    def read_transcript(self, session_id: str) -> list[SessionMessage]:
        path = self.transcript_path(session_id)
        if not path.exists():
            return []
        messages: list[SessionMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            messages.append(SessionMessage.from_dict(json.loads(line)))
        return messages

    def history_messages(self, session_id: str) -> list[dict[str, str]]:
        """Transcript reduced to the ``[{role, content}, ...]`` shape injected into context."""
        return [
            {"role": message.role, "content": message.content}
            for message in self.read_transcript(session_id)
        ]

    def add_turn(self, session_id: str, run_id: str) -> SessionRecord:
        record = self.load(session_id)
        if run_id not in record.turns:
            record.turns.append(run_id)
        self._write_record(record)
        return record

    def set_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        retained_from_seq: int,
    ) -> SessionRecord:
        record = self.load(session_id)
        record.compaction = {
            "summary": summary,
            "retained_from_seq": retained_from_seq,
        }
        self._write_record(record)
        return record

    def _write_record(self, record: SessionRecord) -> None:
        record.updated_at = _now()
        path = self.session_path(record.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_record_file(path: Path) -> SessionRecord:
        return SessionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))