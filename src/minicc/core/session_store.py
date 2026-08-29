"""Session persistence backed by an append-only event log."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from minicc.core.events import event_log_for
from minicc.core.projection_cache import ProjectionCache
from minicc.core.projections import (
    ProjectionRegistry,
    SurfaceProjection,
    default_projections,
)

SESSION_FORMAT_VERSION = 2
MessageRole = Literal["user", "assistant", "tool"]


class SessionNotFoundError(KeyError):
    pass


@dataclass
class SessionMessage:
    seq: int
    role: MessageRole
    content: str
    run_id: str | None = None
    event_type: str = ""

    def to_dict(self):
        return {
            "seq": self.seq,
            "role": self.role,
            "content": self.content,
            "run_id": self.run_id,
            "event_type": self.event_type,
        }


@dataclass
class SessionRecord:
    schema_version: int = SESSION_FORMAT_VERSION
    session_id: str = ""
    project_root: str = ""
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    turns: list[str] = field(default_factory=list)
    compaction: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            schema_version=SESSION_FORMAT_VERSION,
            session_id=str(data.get("session_id", "")),
            project_root=str(data.get("project_root", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            turns=list(data.get("turns", [])),
            compaction=dict(data.get("compaction", {})),
        )


def new_session_id():
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, root: Path | None = None):
        self._root = Path(root or Path.cwd() / ".minicc" / "sessions")

    @property
    def root(self):
        return self._root

    def session_dir(self, sid):
        return self.root / sid

    def session_path(self, sid):
        return self.session_dir(sid) / "session.json"

    def events_path(self, sid):
        return self.session_dir(sid) / "events.jsonl"

    def projection_cache_path(self, sid):
        return self.session_dir(sid) / "projections.json"

    def transcript_path(self, sid):
        return self.session_dir(sid) / "transcript.jsonl"

    def session_runs_dir(self, sid):
        return self.session_dir(sid) / "runs"

    def exists(self, sid):
        return self.session_path(sid).exists()

    def create(self, project_root: Path | str, *, title: str = ""):
        sid = new_session_id()
        now = _now()
        r = SessionRecord(
            session_id=sid,
            project_root=str(Path(project_root).resolve()),
            title=title,
            created_at=now,
            updated_at=now,
        )
        self.session_dir(sid).mkdir(parents=True, exist_ok=True)
        self.session_runs_dir(sid).mkdir(exist_ok=True)
        self._write_record(r)
        self.events_path(sid).touch()
        self.append_event(
            sid,
            "session/start",
            {
                "session_id": sid,
                "project_root": r.project_root,
                "format_version": SESSION_FORMAT_VERSION,
                "created_at": now,
            },
        )
        return r

    def load(self, sid):
        p = self.session_path(sid)
        if not p.exists():
            raise SessionNotFoundError(f"Session not found: {sid}")
        return SessionRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, r):
        self._write_record(r)

    def list_sessions(self):
        if not self.root.exists():
            return []
        return sorted(
            [
                self.load(p.name)
                for p in self.root.iterdir()
                if p.is_dir() and (p / "session.json").exists()
            ],
            key=lambda x: x.updated_at,
            reverse=True,
        )

    def event_log(self, sid):
        if not self.exists(sid):
            raise SessionNotFoundError(sid)
        return event_log_for(self.session_dir(sid), sid)

    def append_event(self, sid, event_type, data=None):
        e = self.event_log(sid).append(event_type, data)
        r = self.load(sid)
        r.updated_at = _now()
        self._write_record(r)
        return e

    def events(self, sid):
        return self.event_log(sid).events

    def repair(self, sid):
        return self.event_log(sid).repair_interrupted()

    def append_message(self, sid, role: MessageRole, content: str, *, run_id: str | None = None):
        typ = "tool/result" if role == "tool" else f"{role}/message"
        d = {"role": role, "content": content}
        if run_id:
            d["run_id"] = run_id
        e = self.append_event(sid, typ, d)
        return SessionMessage(e.seq, role, content, run_id, typ)

    def read_transcript(self, sid):
        out = []
        for e in self.events(sid):
            if e.type not in {"user/message", "assistant/message", "tool/result"}:
                continue
            d = e.data
            role = d.get("role") or ("tool" if e.type == "tool/result" else e.type.split("/")[0])
            msg = d.get("message") if isinstance(d.get("message"), dict) else d
            c = msg.get("content", "") if isinstance(msg, dict) else d.get("content", "")
            if isinstance(c, list):
                c = json.dumps(c, ensure_ascii=False)
            out.append(SessionMessage(e.seq, role, str(c), d.get("run_id"), e.type))
        return out

    def history_messages(self, sid):
        reg = ProjectionRegistry()
        reg.register(SurfaceProjection(), session_id=sid, events=self.events(sid))
        return reg.value(sid, "surface")["messages"]

    def add_turn(self, sid, run_id):
        r = self.load(sid)
        if run_id not in r.turns:
            r.turns.append(run_id)
        self._write_record(r)
        return r

    def rename(self, sid, title):
        r = self.load(sid)
        self.append_event(sid, "session/title", {"title": title})
        r.title = title
        self._write_record(r)
        return r

    def set_compaction(self, sid, *, summary, retained_from_seq):
        r = self.load(sid)
        r.compaction = {"summary": summary, "retained_from_seq": retained_from_seq}
        self._write_record(r)
        return r

    def save_projection_cache(self, sid: str, registry: ProjectionRegistry) -> None:
        ProjectionCache(self.projection_cache_path(sid)).save(registry.cache_rows(sid))

    def projection_registry(self, sid, *, cache=None):
        reg = ProjectionRegistry()
        [reg.register(p) for p in default_projections()]
        events = self.events(sid)
        rows = (
            cache if cache is not None else ProjectionCache(self.projection_cache_path(sid)).load()
        )
        reg.restore_cache(sid, rows, events) if rows else reg.fold(sid, events)
        return reg

    def _write_record(self, r):
        p = self.session_path(r.session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(r.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
        except OSError:
            pass
        tmp.replace(p)
        try:
            dir_fd = os.open(str(p.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
