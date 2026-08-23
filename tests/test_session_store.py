import json

import pytest

from minicc.core.session_store import (
    SessionNotFoundError,
    SessionRecord,
    SessionStore,
)


def test_create_persists_metadata_and_empty_transcript(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")

    assert store.exists(record.session_id)
    assert record.project_root == str((tmp_path / "project").resolve())
    assert record.turns == []

    loaded = store.load(record.session_id)
    assert loaded.session_id == record.session_id
    assert loaded.title == ""

    assert store.read_transcript(record.session_id) == []
    # transcript file physically exists and is empty
    assert store.transcript_path(record.session_id).read_text(encoding="utf-8") == ""


def test_new_session_id_unique(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.create("/tmp/a")
    second = store.create("/tmp/a")
    assert first.session_id != second.session_id


def test_append_and_read_transcript_sequence_monotonic(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")

    store.append_message(record.session_id, "user", "hello", run_id="run-1")
    store.append_message(record.session_id, "assistant", "hi there", run_id="run-1")

    messages = store.read_transcript(record.session_id)
    assert [m.seq for m in messages] == [1, 2]
    assert [m.role for m in messages] == ["user", "assistant"]
    assert [m.content for m in messages] == ["hello", "hi there"]

    assert store.history_messages(record.session_id) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_transcript_appends_are_immutable_lines(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    store.append_message(record.session_id, "user", "first")
    store.append_message(record.session_id, "assistant", "second")

    lines = store.transcript_path(record.session_id).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # each line is a standalone JSON object with the documented shape
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["role"] == "user" and parsed[0]["content"] == "first"
    assert "seq" in parsed[0] and "run_id" in parsed[0]


def test_add_turn_records_run_id_once(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    store.add_turn(record.session_id, "run-1")
    store.add_turn(record.session_id, "run-2")
    store.add_turn(record.session_id, "run-1")  # duplicate ignored

    assert store.load(record.session_id).turns == ["run-1", "run-2"]


def test_rename_and_compaction_update_metadata(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project", title="old")
    assert store.load(record.session_id).title == "old"

    store.rename(record.session_id, "new title")
    assert store.load(record.session_id).title == "new title"

    store.set_compaction(record.session_id, summary="summarized", retained_from_seq=3)
    loaded = store.load(record.session_id)
    assert loaded.compaction == {"summary": "summarized", "retained_from_seq": 3}


def test_list_sessions_returns_all(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.create(tmp_path / "a")
    second = store.create(tmp_path / "b")

    records = store.list_sessions()
    assert {r.session_id for r in records} == {first.session_id, second.session_id}


def test_load_missing_session_raises(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(SessionNotFoundError):
        store.load("does-not-exist")


def test_record_roundtrip_preserves_fields(tmp_path) -> None:
    record = SessionRecord(
        session_id="sid",
        project_root="/p",
        title="t",
        created_at="c",
        updated_at="u",
        turns=["r1"],
        compaction={"summary": "s", "retained_from_seq": 1},
    )
    store = SessionStore(tmp_path / "sessions")
    store.save(record)
    loaded = store.load("sid")
    assert loaded == record