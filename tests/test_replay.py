from __future__ import annotations

import json
from pathlib import Path

from minicc.core.protocol import parse_tool_call
from minicc.core.provider import ModelUsage, NativeToolCall
from minicc.core.state import Observation, RunState, save_run_state
from minicc.trace.recorder import TraceRecorder
from minicc.trace.replay import (
    compare_fresh_replay,
    create_replay_case,
    create_replay_case_from_eval_case,
    run_deterministic_replay,
)


def _make_run(root: Path, *, run_id: str = "run-source") -> Path:
    run_dir = root / run_id
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    (artifacts / "diff.patch").write_text("", encoding="utf-8")
    (workspace / "hello.txt").write_text("before\n", encoding="utf-8")
    state = RunState.start(
        "Inspect hello.txt and report the result.",
        run_dir=run_dir,
        artifacts_dir=artifacts,
        workspace_host_path=workspace,
    )
    state.run_id = run_id
    state.status = "completed"
    state.metrics["profile"] = "baseline-bash"
    state.metrics["completion_verifier_commands"] = ["python -m pytest -q"]
    state.metrics["completion_verifier_timeout_sec"] = 30
    trace = TraceRecorder(run_dir / "trace.jsonl")
    trace.run_started(state)

    bash_call = NativeToolCall(id="b1", name="bash", arguments=json.dumps({"command": "cat hello.txt"}))
    trace.model_response(state, "", 1, ModelUsage(), tool_calls=(bash_call,))
    bash = parse_tool_call(bash_call.id, bash_call.name, json.loads(bash_call.arguments))
    trace.action_parsed(state, bash)
    trace.action_started(state, bash)
    trace.sandbox_exec_started(state, str(bash.arguments["command"]))
    observation = Observation(kind="command_result", exit_code=0, stdout_preview="before\n")
    trace.sandbox_exec_finished(state, observation)
    trace.observation_created(state, observation)

    final_call = NativeToolCall(id="f1", name="final", arguments=json.dumps({"answer": "before"}))
    trace.model_response(state, "", 1, ModelUsage(), tool_calls=(final_call,))
    final = parse_tool_call(final_call.id, final_call.name, json.loads(final_call.arguments))
    trace.action_parsed(state, final)
    trace.action_started(state, final)
    trace.run_completed(state)
    save_run_state(state)
    (run_dir / "metrics.json").write_text(json.dumps(state.metrics), encoding="utf-8")
    return run_dir


def test_create_and_run_deterministic_replay(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    case = create_replay_case(run_dir, output_dir=tmp_path / "replays")

    manifest = json.loads((case / "case.json").read_text(encoding="utf-8"))
    assert manifest["deterministic_eligible"] is True
    assert manifest["fresh_eligible"] is True
    assert manifest["verification_commands"] == ["python -m pytest -q"]
    result = run_deterministic_replay(case)
    assert result.passed is True
    assert result.report["scorecard"]["checks_total"] == 7
    assert (case / "deterministic_replay_report.md").is_file()


def test_deterministic_replay_rejects_tampered_bundle(tmp_path: Path) -> None:
    case = create_replay_case(_make_run(tmp_path), output_dir=tmp_path / "replays")
    trace = case / "trace.jsonl"
    trace.write_text(trace.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_deterministic_replay(case)
    assert result.passed is False
    assert result.report["checks"]["artifact_manifest"]["passed"] is False


def test_fresh_replay_comparison_writes_scorecard(tmp_path: Path) -> None:
    source = _make_run(tmp_path, run_id="source")
    case = create_replay_case(source, output_dir=tmp_path / "replays")
    fresh = _make_run(tmp_path, run_id="fresh")
    result = compare_fresh_replay(case, fresh, output_dir=tmp_path / "reports")
    assert result.passed is True
    assert result.report["scorecard"]["overall"] == 1.0
    assert (tmp_path / "reports" / "fresh_replay_report.json").is_file()


def test_eval_case_seed_is_fresh_replay_eligible(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "task.py").write_text("print('ok')\n", encoding="utf-8")
    case_yaml = tmp_path / "case.yaml"
    case_yaml.write_text(
        "name: demo\nprompt: Run the task.\nfixture: fixture\nassertions: []\n",
        encoding="utf-8",
    )
    case = create_replay_case_from_eval_case(case_yaml, output_dir=tmp_path / "replays")
    manifest = json.loads((case / "case.json").read_text(encoding="utf-8"))
    assert manifest["source_kind"] == "eval_case"
    assert manifest["fresh_eligible"] is True
    assert run_deterministic_replay(case).passed is False
