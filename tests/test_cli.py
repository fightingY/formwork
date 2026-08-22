import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from minicc import __version__, cli
from minicc.config import (
    AuxModelConfig,
    BudgetSettings,
    ContextSettings,
    PolicySettings,
    ProviderRoute,
    SandboxSettings,
    Settings,
)
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import Observation, RunState, save_run_state


class FakeProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text=self.responses.pop(0),
            raw={},
            usage=ModelUsage(prompt_tokens=5, completion_tokens=2),
            latency_ms=3,
        )


class FakeExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def run(self, action: BashAction, state: RunState) -> Observation:
        return Observation(kind="command_result", exit_code=0, stdout_preview="ok")


class FakeLoop:
    def __init__(self, result_state: RunState) -> None:
        self.result_state = result_state

    def run(self, state: RunState):
        state.status = "completed"
        state.final_answer = "done"
        return type("LoopResult", (), {"state": state})()


def _settings(**overrides) -> Settings:
    """Build V4.1-schema settings with one valid default route.

    ``_build_loop`` reads ``settings.default_route`` (json_mode, child route) and
    report commands read ``_provider_summary``, so the default route must resolve.
    """
    route = ProviderRoute(
        name="default",
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )
    kwargs = {
        "providers": {"default": route},
        "default_provider": "default",
        "sandbox": SandboxSettings(),
        "budget": BudgetSettings(),
        "context": ContextSettings(),
        "policy": PolicySettings(),
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def _settings_with_aux(model: str | None = None) -> tuple[Settings, ProviderRoute]:
    aux_route = ProviderRoute(
        name="aux",
        base_url="https://aux.test/v1",
        api_key="key",
        model="aux-model",
    )
    settings = _settings(
        providers={"default": _settings().default_route, "aux": aux_route},
        aux=AuxModelConfig(provider="aux", model=model),
    )
    return settings, aux_route


def test_aux_route_defaults_to_default_route() -> None:
    settings = _settings()
    assert cli._aux_route(settings) is settings.default_route
    assert cli._aux_model(settings) == "model"


def test_aux_route_uses_configured_provider() -> None:
    settings, aux_route = _settings_with_aux()
    assert cli._aux_route(settings) is aux_route
    assert cli._aux_model(settings) == "aux-model"


def test_aux_model_prefers_override() -> None:
    settings, _ = _settings_with_aux(model="aux-override")
    assert cli._aux_model(settings) == "aux-override"


def test_reconfigure_std_streams_allows_emoji_on_gbk_console(monkeypatch) -> None:
    # A Windows console defaults to the GBK codec, which cannot encode the
    # emoji that model-generated final answers sometimes contain. Without the
    # reconfigure, ``print`` would raise UnicodeEncodeError after the run is
    # already persisted. Re-pointing stdout/stderr at UTF-8 must prevent that.
    out = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    err = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert sys.stdout.encoding == "gbk"

    cli._reconfigure_std_streams()

    assert sys.stdout.encoding == "utf-8"
    assert sys.stderr.encoding == "utf-8"
    print("found: \U0001F534 critical \U0001F7E0 high")
    sys.stdout.flush()
    out.flush()
    out.close()
    err.close()


def test_run_command_fake_provider_writes_complete_evidence_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _settings()
    provider = FakeProvider(
        [
            '{"type":"bash","command":"echo ok","purpose":"smoke test"}',
            '{"type":"final","answer":"done"}',
        ]
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: provider)
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.run_command(
        argparse.Namespace(
            goal="fake provider smoke test",
            execute_local=True,
            no_workspace_copy=False,
            docker_image=None,
            stream=None,
        )
    )

    run_dirs = [path for path in (tmp_path / ".minicc" / "runs").iterdir() if path.is_dir()]
    assert exit_code == 0
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for relative_path in [
        "state.json",
        "trace.jsonl",
        "metrics.json",
        "repository_profile.json",
        "workspace_manifest.json",
        "artifacts/diff.patch",
        "run_report.json",
        "run_report.md",
    ]:
        assert (run_dir / relative_path).exists()
    assert (tmp_path / ".minicc" / "artifacts" / run_dir.name / "manifest.json").exists()


def test_run_source_dir_keeps_external_repository_unchanged(tmp_path, monkeypatch) -> None:
    harness_root = tmp_path / "harness"
    source_root = tmp_path / "external-project"
    harness_root.mkdir()
    source_root.mkdir()
    (source_root / "pom.xml").write_text("<project />\n", encoding="utf-8")
    original = (source_root / "pom.xml").read_bytes()
    monkeypatch.chdir(harness_root)
    settings = _settings()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: FakeProvider(['{"type":"final","answer":"done"}']),
    )
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.run_command(
        argparse.Namespace(
            goal="inspect external project",
            source_dir=source_root,
            execute_local=True,
            no_workspace_copy=False,
            docker_image=None,
            stream=None,
        )
    )

    run_dirs = [path for path in (harness_root / ".minicc" / "runs").iterdir() if path.is_dir()]
    state = json.loads((run_dirs[0] / "state.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert (source_root / "pom.xml").read_bytes() == original
    assert not (source_root / ".minicc").exists()
    assert state["metrics"]["source_workspace_unchanged"] is True
    assert state["repository_profile"]["workspace_kind"] == "maven"


def test_run_command_binds_completion_verifier(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _settings()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: FakeProvider(['{"type":"final","answer":"verified"}']),
    )
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.run_command(
        argparse.Namespace(
            goal="finish only after tests pass",
            source_dir=None,
            execute_local=True,
            no_workspace_copy=False,
            docker_image=None,
            stream=None,
            verify_command=["python -m pytest -q"],
            verification_timeout_sec=45,
        )
    )

    run_dir = next(path for path in (tmp_path / ".minicc" / "runs").iterdir() if path.is_dir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert state["metrics"]["verification_attempts"] == 1
    assert state["metrics"]["verification_passed"] == 1
    assert len(state["metrics"]["completion_verifier_sha256"]) == 64
    assert "python -m pytest -q" in state["constraints"][0]


def test_eval_command_writes_one_suite_run_artifact_index_and_version_pointer(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    case_dir = tmp_path / "eval_cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "README.md").write_text("ready\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        "name: demo\n"
        "prompt: Finish.\n"
        "assertions:\n"
        "  - type: trace_action_shape\n"
        "    actions:\n"
        "      - command: echo ok\n"
        "        expect_exit_code: 0\n",
        encoding="utf-8",
    )
    settings = _settings()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: FakeProvider(
            [
                '{"type":"bash","command":"echo ok"}',
                '{"type":"final","answer":"done"}',
            ]
        ),
    )
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.eval_command(
        argparse.Namespace(
            path=tmp_path / "eval_cases",
            milestone="stable-v2.0.2",
            execute_local=True,
            repeat=1,
            output_dir=None,
            case_names=["demo"],
            release_gate=False,
            cache_variant="p1",
            cache_sequence_id="round-test",
            execution_order="p0-first",
        )
    )

    suites = list((tmp_path / ".minicc" / "suites").iterdir())
    runs = list((tmp_path / ".minicc" / "runs").iterdir())
    assert exit_code == 0
    assert len(suites) == 1
    assert len(runs) == 1
    assert {path.name for path in suites[0].iterdir()} == {
        "manifest.json",
        "report.json",
        "report.md",
        "report.csv",
    }
    assert (tmp_path / ".minicc" / "artifacts" / runs[0].name / "manifest.json").exists()
    version = json.loads(
        (tmp_path / ".minicc" / "versions" / "stable-v2.0.2" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert version["entry_count"] == 1
    assert version["entries"][0]["suite_id"] == suites[0].name
    assert version["entries"][0]["evidence_valid"] is True
    suite_report = json.loads((suites[0] / "report.json").read_text(encoding="utf-8"))
    assert cli.load_cache_suite_report(
        suites[0] / "report.json",
        verify_manifest=True,
    )["suite_id"] == suites[0].name
    assert suite_report["configuration"]["cache_variant"] == "p1"
    assert suite_report["configuration"]["cache_sequence_id"] == "round-test"
    assert suite_report["configuration"]["execution_order"] == "p0-first"
    assert suite_report["configuration"]["prompt_layout"] == "append"
    case_record = suite_report["cases"][0]
    assert case_record["case_source_path"] == "eval_cases/demo/case.yaml"
    assert case_record["fixture_source_path"] == "eval_cases/demo/fixture"
    assert len(case_record["request_rows"]) == 2
    assert case_record["trace_assertion_events"][0]["action"]["command"] == (
        "echo ok"
    )
    assert suite_report["created_at"]
    artifact_index_path = (
        tmp_path / ".minicc" / "artifacts" / runs[0].name / "manifest.json"
    )
    artifact_index = json.loads(artifact_index_path.read_text(encoding="utf-8"))
    assert {
        "state",
        "trace",
        "metrics",
        "workspace_manifest",
        "diff",
        "run_report",
    }.issubset(artifact_index["artifacts"])
    assert len(artifact_index["artifacts"]["metrics"]["sha256"]) == 64

    report_path = suites[0] / "report.json"
    manifest_path = suites[0] / "manifest.json"
    original_report = report_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    trace_path = Path(case_record["evidence"]["trace"])
    run_report_path = Path(case_record["evidence"]["run_report"])
    original_trace = trace_path.read_bytes()
    original_run_report = run_report_path.read_bytes()
    original_index = artifact_index_path.read_bytes()
    changed_events = [
        json.loads(line)
        for line in original_trace.decode("utf-8").splitlines()
        if line.strip()
    ]
    model_response = next(
        event for event in changed_events if event.get("event") == "model_response"
    )
    model_response["usage"]["cache_hit_tokens"] = 999
    changed_trace = (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in changed_events
        )
        + "\n"
    ).encode("utf-8")
    trace_path.write_bytes(changed_trace)
    changed_index = json.loads(original_index)
    changed_index["artifacts"]["trace"].update(
        {
            "bytes": len(changed_trace),
            "sha256": hashlib.sha256(changed_trace).hexdigest(),
        }
    )
    artifact_index_path.write_text(
        json.dumps(changed_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="request rows"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    trace_path.write_bytes(original_trace)
    artifact_index_path.write_bytes(original_index)

    changed_events = [
        json.loads(line)
        for line in original_trace.decode("utf-8").splitlines()
        if line.strip()
    ]
    bash_event = next(
        event
        for event in changed_events
        if event.get("event") == "action_parsed"
        and (event.get("action") or {}).get("type") == "bash"
    )
    bash_event["action"]["command"] = "echo tampered"
    changed_trace = (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in changed_events
        )
        + "\n"
    ).encode("utf-8")
    changed_run_report = json.loads(original_run_report)
    changed_run_report["trace_assertion_events"][0]["action"]["command"] = (
        "echo tampered"
    )
    changed_run_report_bytes = (
        json.dumps(changed_run_report, ensure_ascii=False, indent=2)
    ).encode("utf-8")
    forged_report = json.loads(original_report)
    forged_report["cases"][0]["trace_assertion_events"][0]["action"][
        "command"
    ] = "echo tampered"
    forged_report_bytes = (
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    trace_path.write_bytes(changed_trace)
    run_report_path.write_bytes(changed_run_report_bytes)
    changed_index = json.loads(original_index)
    changed_index["artifacts"]["trace"].update(
        {
            "bytes": len(changed_trace),
            "sha256": hashlib.sha256(changed_trace).hexdigest(),
        }
    )
    changed_index["artifacts"]["run_report"].update(
        {
            "bytes": len(changed_run_report_bytes),
            "sha256": hashlib.sha256(changed_run_report_bytes).hexdigest(),
        }
    )
    artifact_index_path.write_text(
        json.dumps(changed_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_bytes(forged_report_bytes)
    forged_manifest = json.loads(original_manifest)
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="action shape"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    trace_path.write_bytes(original_trace)
    run_report_path.write_bytes(original_run_report)
    artifact_index_path.write_bytes(original_index)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    forged_report = json.loads(original_report)
    forged_report["cases"][0]["assertions"] = [
        {
            "type": "trace_action_shape",
            "passed": True,
            "message": "forged assertion result",
            "spec_sha256": "0" * 64,
        }
    ]
    report_path.write_text(
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    forged_manifest = json.loads(original_manifest)
    forged_report_bytes = report_path.read_bytes()
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="action shape"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    forged_report = json.loads(original_report)
    forged_report["cases"][0]["metrics"]["prompt_tokens"] += 1
    report_path.write_text(
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    forged_manifest = json.loads(original_manifest)
    forged_report_bytes = report_path.read_bytes()
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run report does not match"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    (runs[0] / "metrics.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        cli.load_cache_suite_report(
            suites[0] / "report.json",
            verify_manifest=True,
        )


def test_eval_parser_accepts_cache_variant() -> None:
    args = cli.build_parser().parse_args(["eval", "--cache-variant", "p2"])

    assert args.cache_variant == "p2"


def test_eval_parser_accepts_guidance_variant() -> None:
    args = cli.build_parser().parse_args(
        [
            "eval",
            "eval_cases/guidance_suite_v1",
            "--guidance-variant",
            "a1",
            "--guidance-sequence-id",
            "round-1",
            "--guidance-execution-order",
            "a0-first",
        ]
    )

    assert args.guidance_variant == "a1"
    assert args.guidance_sequence_id == "round-1"
    assert args.guidance_feedback_path == "guidance/feedback_rules.jsonl"


def test_cache_utilization_parser_collects_exact_round_inputs(tmp_path) -> None:
    args = cli.build_parser().parse_args(
        [
            "cache-utilization-report",
            "--p1-probe",
            str(tmp_path / "p1-r1.json"),
            "--p2-probe",
            str(tmp_path / "p2-r1.json"),
            "--p1-eval",
            str(tmp_path / "p1-e1.json"),
            "--p2-eval",
            str(tmp_path / "p2-e1.json"),
            "--output-dir",
            str(tmp_path / "acceptance"),
        ]
    )

    assert args.p1_probe == [tmp_path / "p1-r1.json"]
    assert args.p2_probe == [tmp_path / "p2-r1.json"]


def test_cache_experiment_loop_disables_mutable_feedback_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _settings(context=ContextSettings(prompt_layout="append"))
    state = RunState.start(
        "cache experiment",
        prompt_namespace="cache-experiment/round-1",
    )

    loop = cli._build_loop(
        FakeProvider(['{"type":"final","answer":"done"}']),
        FakeExecutor(),
        settings=settings,
        state=state,
    )

    assert loop.context_builder.feedback_memory is None


def test_cache_probe_command_writes_canonical_probe_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _settings()
    provider = FakeProvider(['{"type":"bash","command":"true"}'] * 5)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: provider)
    monkeypatch.setattr(cli, "_git_evidence", lambda cwd: ("abc123", False))

    exit_code = cli.cache_probe_command(
        argparse.Namespace(
            cache_variant="p1",
            repeat=5,
            milestone="v2.1.1-test",
            execution_order="p0-first",
            cache_sequence_id="round-1",
            release_gate=True,
        )
    )

    probe_dirs = list((tmp_path / ".minicc" / "cache-probes").iterdir())
    assert exit_code == 0
    assert len(probe_dirs) == 1
    report = json.loads((probe_dirs[0] / "report.json").read_text(encoding="utf-8"))
    assert report["stage"] == "formal_acceptance"
    assert report["configuration"]["cache_variant"] == "p1"
    assert report["configuration"]["cache_sequence_id"] == "round-1"
    assert report["configuration"]["prompt_layout"] == "append"
    assert report["configuration"]["dynamic_sequence_sha256"]
    assert report["stable_prefix"]["estimated_tokens_min"] > 0


def test_cache_probe_release_gate_requires_clean_commit_and_five_requests() -> None:
    args = argparse.Namespace(repeat=5, execution_order="p0-first")

    assert cli._cache_probe_release_gate_error(args, "abc123", False) == ""
    assert "uncommitted" in cli._cache_probe_release_gate_error(args, "abc123", True)
    assert "--repeat 5" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=4),
        "abc123",
        False,
    )
    assert "--execution-order" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=5, execution_order=None),
        "abc123",
        False,
    )
    assert (
        cli._cache_probe_release_gate_error(
            argparse.Namespace(repeat=12, execution_order="p2-first"),
            "abc123",
            False,
            milestone="stable-v2.1.2",
        )
        == ""
    )
    assert "--repeat 12" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=5, execution_order="p1-first"),
        "abc123",
        False,
        milestone="stable-v2.1.2",
    )


def test_memory_release_gate_requires_canonical_formal_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    case_name = "M01_service_contract_follow_up"
    relative_case, _ = cli.REQUIRED_MEMORY_CASES[case_name]
    case_path = tmp_path / relative_case
    case_path.parent.mkdir(parents=True)
    case_path.write_text("name: source\n", encoding="utf-8")
    args = argparse.Namespace(
        path=case_path,
        repeat=3,
        execution_order="alternating",
        execute_local=False,
    )
    image = "python:test@sha256:abc123"

    assert (
        cli._memory_release_gate_error(
            args,
            "abc123",
            False,
            image,
            milestone="v2.2-acceptance",
            case_name=case_name,
        )
        == ""
    )
    assert "uncommitted" in cli._memory_release_gate_error(
        args,
        "abc123",
        True,
        image,
        milestone="v2.2-acceptance",
        case_name=case_name,
    )
    assert "Docker" in cli._memory_release_gate_error(
        argparse.Namespace(**{**vars(args), "execute_local": True}),
        "abc123",
        False,
        image,
        milestone="v2.2-acceptance",
        case_name=case_name,
    )
    assert "--repeat 3" in cli._memory_release_gate_error(
        argparse.Namespace(**{**vars(args), "repeat": 2}),
        "abc123",
        False,
        image,
        milestone="v2.2-acceptance",
        case_name=case_name,
    )
    assert "alternating" in cli._memory_release_gate_error(
        argparse.Namespace(**{**vars(args), "execution_order": "m0-first"}),
        "abc123",
        False,
        image,
        milestone="v2.2-acceptance",
        case_name=case_name,
    )
    assert "v2.2-acceptance" in cli._memory_release_gate_error(
        args,
        "abc123",
        False,
        image,
        milestone="development",
        case_name=case_name,
    )
    assert "canonical case path" in cli._memory_release_gate_error(
        argparse.Namespace(**{**vars(args), "path": tmp_path / "copy" / "case.yaml"}),
        "abc123",
        False,
        image,
        milestone="v2.2-acceptance",
        case_name=case_name,
    )


def test_memory_report_requires_exactly_three_sources(tmp_path, capsys) -> None:
    exit_code = cli.memory_report_command(
        argparse.Namespace(report=[tmp_path / "one.json"], output_dir=tmp_path / "acceptance")
    )

    assert exit_code == 2
    assert "exactly three" in capsys.readouterr().err


def test_cache_report_does_not_write_failed_acceptance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_cache_probe_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "load_cache_suite_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        cli,
        "build_cache_ab_report",
        lambda rounds: {"status": "FAIL", "passed": False},
    )
    output_dir = tmp_path / "acceptance" / "stable-v2.1.1"

    exit_code = cli.cache_report_command(
        argparse.Namespace(
            p0_probe=[tmp_path / "p0-r1.json", tmp_path / "p0-r2.json"],
            p1_probe=[tmp_path / "p1-r1.json", tmp_path / "p1-r2.json"],
            p0_eval=[tmp_path / "p0-e1.json", tmp_path / "p0-e2.json"],
            p1_eval=[tmp_path / "p1-e1.json", tmp_path / "p1-e2.json"],
            output_dir=output_dir,
        )
    )

    assert exit_code == 1
    assert not output_dir.exists()


def test_cache_utilization_report_does_not_write_failed_acceptance(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "load_cache_probe_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "load_cache_suite_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        cli,
        "build_cache_utilization_report",
        lambda rounds: {
            "status": "FAIL",
            "passed": False,
            "criteria": {"all_rounds_passed": False},
            "rounds": [],
        },
    )
    output_dir = tmp_path / "acceptance" / "stable-v2.1.2"
    two = [tmp_path / "r1.json", tmp_path / "r2.json"]

    exit_code = cli.cache_utilization_report_command(
        argparse.Namespace(
            p1_probe=two,
            p2_probe=two,
            p1_eval=two,
            p2_eval=two,
            output_dir=output_dir,
        )
    )

    assert exit_code == 1
    assert not output_dir.exists()


def test_resume_command_uses_normal_settings_after_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "run-1"
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    state = RunState.start(
        "resume",
        workspace_host_path=workspace,
        run_dir=run_dir,
        artifacts_dir=artifacts,
    )
    state.run_id = "run-1"
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="echo ok")
    state.approvals.append({"status": "approved", "action": "echo ok"})
    save_run_state(state)

    settings = _settings()
    loop_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: FakeProvider())
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    def fake_build_loop(provider, executor, *, settings, session=None, state=None, stream=None):
        loop_calls.append({"settings": settings, "state": state})
        return FakeLoop(state)

    monkeypatch.setattr(cli, "_build_loop", fake_build_loop)

    exit_code = cli.resume_command(argparse.Namespace(run_id="run-1", execute_local=True))

    assert exit_code == 0
    assert loop_calls
    assert loop_calls[0]["settings"] is settings


def test_resume_command_denial_terminates_without_agent_loop(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "run-denied"
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    state = RunState.start(
        "resume denied",
        workspace_host_path=workspace,
        run_dir=run_dir,
        artifacts_dir=artifacts,
    )
    state.run_id = "run-denied"
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="rm -r tmp_build")
    state.approvals.append({"status": "denied", "reason": "too risky", "action": "rm -r tmp_build"})
    save_run_state(state)

    settings = _settings()
    loop_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: FakeProvider())
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_build_loop", lambda *args, **kwargs: loop_calls.append(kwargs))

    exit_code = cli.resume_command(argparse.Namespace(run_id="run-denied", execute_local=True))

    assert exit_code == 1
    assert loop_calls == []
    saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["pending_action"] is None
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "approval_resolved" in trace_text
    assert "denied" in trace_text


def test_release_gate_requires_clean_docker_commit_and_repeat_matrix() -> None:
    valid = argparse.Namespace(execute_local=False, repeat=3, case_names=["C01", "C02"])

    assert cli._release_gate_error(valid, "abc123", False) == ""
    assert "uncommitted" in cli._release_gate_error(valid, "abc123", True)
    assert "Docker" in cli._release_gate_error(
        argparse.Namespace(execute_local=True, repeat=3, case_names=["C01"]),
        "abc123",
        False,
    )
    assert "--repeat 3" in cli._release_gate_error(
        argparse.Namespace(execute_local=False, repeat=2, case_names=["C01"]),
        "abc123",
        False,
    )


def test_v212_release_gate_locks_canonical_suite_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    valid = argparse.Namespace(
        execute_local=False,
        repeat=3,
        case_names=["C02_fix_failing_test", "C07_large_log_debugging"],
        path=tmp_path / "other-suite",
    )

    assert "capability_suite_v1" in cli._release_gate_error(
        valid,
        "abc123",
        False,
        milestone="v2.1.2-development",
    )
    valid.path = tmp_path / "eval_cases" / "capability_suite_v1"
    assert (
        cli._release_gate_error(
            valid,
            "abc123",
            False,
            milestone="v2.1.2-development",
        )
        == ""
    )
    valid.repeat = 4
    assert "exactly --repeat 3" in cli._release_gate_error(
        valid,
        "abc123",
        False,
        milestone="v2.1.2-development",
    )


def test_v30_release_gate_locks_fixed_five_case_matrix(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    case_names = [
        "C01_repo_onboarding",
        "C02_fix_failing_test",
        "C03_add_cli_option",
        "C04_add_regression_test",
        "C09_hitl_destructive_command",
    ]
    valid = argparse.Namespace(
        execute_local=False,
        repeat=3,
        case_names=case_names,
        path=tmp_path / "eval_cases" / "capability_suite_v1",
    )

    assert (
        cli._release_gate_error(
            valid,
            "abc123",
            False,
            milestone="v3.0-acceptance",
        )
        == ""
    )
    valid.case_names = case_names[:-1]
    assert "exact C01/C02/C03/C04/C09" in cli._release_gate_error(
        valid,
        "abc123",
        False,
        milestone="v3.0-acceptance",
    )


def test_v30_release_report_gate_requires_current_formal_suite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_git_formal_state_error", lambda root: "")
    monkeypatch.setattr(cli, "_formal_system_case_eligible", lambda row, suite_id: True)
    case_names = [
        "C01_repo_onboarding",
        "C02_fix_failing_test",
        "C03_add_cli_option",
        "C04_add_regression_test",
        "C09_hitl_destructive_command",
    ]
    report = {
        "stage": "formal_acceptance",
        "passed": True,
        "repeat": 3,
        "configuration": {
            "git_commit": "abc123",
            "worktree_dirty": False,
            "release_gate": True,
            "git_preflight_verified": True,
            "git_postflight_verified": True,
            "docker_image": "python@sha256:abc123",
        },
        "cases": [
            {
                "name": name,
                "run_id": f"{name}-r{attempt}",
                "formal_metric_eligible": True,
            }
            for attempt in range(1, 4)
            for name in case_names
        ],
    }
    output = tmp_path / "acceptance" / "stable-v3.0"

    assert (
        cli._release_report_gate_error(
            project_root=tmp_path,
            source_commit="abc123",
            worktree_dirty=False,
            output_dir=output,
            system_report=report,
        )
        == ""
    )
    report["configuration"]["git_postflight_verified"] = False
    assert "current formal commit" in cli._release_report_gate_error(
        project_root=tmp_path,
        source_commit="abc123",
        worktree_dirty=False,
        output_dir=output,
        system_report=report,
    )


def test_formal_system_case_eligibility_recomputes_waiting_hitl_run(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    monkeypatch.setattr(
        cli,
        "inspect_run",
        lambda path: {
            "formal_metric_eligible": True,
            "run_id": "run-1",
            "suite_id": "suite-1",
            "stage": "formal_acceptance",
            "status": "waiting_approval",
        },
    )

    assert cli._formal_system_case_eligible(
        {"run_id": "run-1", "run_dir": str(run_dir), "formal_metric_eligible": False},
        suite_id="suite-1",
    ) is True


def test_v212_eval_rejects_external_fixture_before_provider(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    suite_root = tmp_path / "eval_cases" / "capability_suite_v1"
    for name in ("C02_fix_failing_test", "C07_large_log_debugging"):
        case_dir = suite_root / name
        fixture = tmp_path / "outside" / name
        case_dir.mkdir(parents=True)
        fixture.mkdir(parents=True)
        (fixture / "README.md").write_text("external\n", encoding="utf-8")
        (case_dir / "case.yaml").write_text(
            f"name: {name}\n"
            "prompt: Finish.\n"
            f"fixture: ../../../outside/{name}\n"
            "assertions: []\n",
            encoding="utf-8",
        )
    settings = _settings(
        sandbox=SandboxSettings(
            image="python@sha256:" + ("a" * 64),
        ),
    )
    provider_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_git_evidence", lambda cwd: ("abc123", False))
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: provider_calls.append(loaded) or FakeProvider(),
    )

    exit_code = cli.eval_command(
        argparse.Namespace(
            path=suite_root,
            milestone="v2.1.2-development",
            execute_local=False,
            repeat=3,
            output_dir=None,
            case_names=["C02_fix_failing_test", "C07_large_log_debugging"],
            release_gate=True,
            cache_variant="p1",
            cache_sequence_id="external-fixture",
            execution_order="p1-first",
        )
    )

    assert exit_code == 2
    assert provider_calls == []


def test_git_index_flags_rejects_skip_worktree(tmp_path) -> None:
    tracked = tmp_path / "tracked.py"
    tracked.write_text("VALUE = 'committed'\n", encoding="utf-8")
    for args in (
        ("init",),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "baseline"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

    assert cli._git_index_flags_error(tmp_path) == ""
    subprocess.run(
        ["git", "update-index", "--skip-worktree", "tracked.py"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    assert "skip-worktree" in cli._git_index_flags_error(tmp_path)


def test_git_formal_state_rejects_ambient_content_transform_attributes(
    tmp_path,
) -> None:
    source = tmp_path / "src" / "runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 'committed'\n", encoding="utf-8")
    for args in (
        ("init",),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "runtime baseline"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

    assert cli._git_transform_attributes_error(tmp_path) == ""
    info_attributes = tmp_path / ".git" / "info" / "attributes"
    info_attributes.write_text(
        "src/runtime.py filter=lossy\n",
        encoding="utf-8",
    )

    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""
    assert "filter=lossy" in cli._git_transform_attributes_error(tmp_path)


def test_cleanup_command_defaults_to_dry_run_and_apply_uses_same_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        '{"run_id":"old-run","goal":"old","status":"failed"}',
        encoding="utf-8",
    )
    old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(run_dir, (old, old))

    assert cli.cleanup_command(argparse.Namespace(older_than_hours=24, apply=False)) == 0
    assert run_dir.exists()
    assert cli.cleanup_command(argparse.Namespace(older_than_hours=24, apply=True)) == 0
    assert not run_dir.exists()


def test_cli_version_is_prerelease_dev() -> None:
    # V4.1 开发期仍是 pre-release（.dev0），归档时再定正式版本号。
    assert __version__ == "3.7.0.dev0"


def test_models_command_prints_discovered_models(monkeypatch, capsys) -> None:
    from minicc.core.discovery import ModelInfo

    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr(
        "minicc.core.discovery.discover_models",
        lambda base_url, api_key, *, headers=None, timeout_ms=120000, max_bytes=4194304: [
            ModelInfo(id="a", context_window=131072, max_output_tokens=8192),
            ModelInfo(id="b"),
        ],
    )

    code = cli.models_command(argparse.Namespace(route=None, probe_key=None, json_output=False))

    out = capsys.readouterr().out
    assert code == 0
    assert "a  context_window=131072  max_output_tokens=8192" in out
    assert "\nb\n" in out


def test_models_command_json_output(monkeypatch, capsys) -> None:
    from minicc.core.discovery import ModelInfo

    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr(
        "minicc.core.discovery.discover_models",
        lambda *args, **kwargs: [ModelInfo(id="a", context_window=131072, max_output_tokens=8192)],
    )

    code = cli.models_command(
        argparse.Namespace(route="default", probe_key="tmp", json_output=True)
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out) == [
        {"id": "a", "context_window": 131072, "max_output_tokens": 8192}
    ]
