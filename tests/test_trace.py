import json

from minicc.core.protocol import PROTOCOL_SCHEMA_VERSION
from minicc.core.state import Observation, RunState
from minicc.trace.metrics import metrics_snapshot, write_metrics
from minicc.trace.recorder import TraceRecorder


def test_trace_recorder_writes_jsonl_events(tmp_path) -> None:
    state = RunState.start("trace", run_dir=tmp_path)
    recorder = TraceRecorder(tmp_path / "trace.jsonl")

    recorder.run_started(state)
    recorder.observation_created(
        state,
        Observation(kind="command_result", exit_code=0, artifact_ids=["art_0001"], message="ok"),
    )

    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "run_started"
    assert events[1]["event"] == "observation_created"
    assert events[2]["event"] == "artifact_written"
    assert events[2]["artifact_id"] == "art_0001"


def test_write_metrics_persists_snapshot(tmp_path) -> None:
    state = RunState.start("metrics", run_dir=tmp_path)
    state.metrics["turns"] = 2

    path = write_metrics(state)

    assert path == tmp_path / "metrics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == metrics_snapshot(state)
    assert data["turns"] == 2
    assert data["run_id"] == state.run_id
    assert data["schema_version"] == PROTOCOL_SCHEMA_VERSION
    assert data["suite_id"] is None
