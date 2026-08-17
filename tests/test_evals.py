import json
import subprocess

import pytest

from minicc.cli import (
    _attach_repository_context,
    _case_constraints,
    _completion_verifier_for_case,
    _settings_for_eval_case,
)
from minicc.config import (
    BudgetSettings,
    ContextSettings,
    PolicySettings,
    ProviderSettings,
    SandboxSettings,
    Settings,
)
from minicc.core.state import RunState
from minicc.core.verification import CommandCompletionVerifier
from minicc.evals import assertions
from minicc.evals.assertions import run_assertions
from minicc.evals.case import discover_cases, load_case
from minicc.evals.runner import (
    _format_infrastructure_error,
    aggregate_case_results,
    run_eval_suite,
    write_eval_report,
    write_suite_report,
)


def test_load_and_discover_eval_cases(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: demo
capability: repo_understanding
prompt: Write docs.
context:
  max_prompt_chars: 1000
  retention_markers: [README.md]
completion_gate: true
assertions:
  - type: file_exists
    path: README.md
""",
        encoding="utf-8",
    )

    case = load_case(case_dir / "case.yaml")

    assert case.name == "demo"
    assert case.fixture_dir == fixture.resolve()
    assert case.writable_paths is None
    assert case.context == {"max_prompt_chars": 1000, "retention_markers": ["README.md"]}
    assert case.completion_gate is True
    assert discover_cases(tmp_path / "cases") == [case]


def test_repository_context_is_written_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    run_dir = tmp_path / "run"
    workspace.mkdir()
    run_dir.mkdir()
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    state = RunState.start("Inspect", workspace_host_path=workspace, run_dir=run_dir)

    _attach_repository_context(state, workspace)

    assert state.repository_profile["workspace_kind"] == "python"
    assert (run_dir / "repository_profile.json").is_file()
    assert not (workspace / "repository_profile.json").exists()
    assert len(state.metrics["repository_profile_sha256"]) == 64


def test_completion_verifier_requires_explicit_case_flag_and_command(tmp_path) -> None:
    case_dir = tmp_path / "case"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    case_path = case_dir / "case.yaml"
    case_path.write_text(
        """
prompt: Verify the task.
completion_gate: true
assertions:
  - type: command
    command: python -m unittest
    expect_exit_code: 0
""",
        encoding="utf-8",
    )

    verifier = _completion_verifier_for_case(load_case(case_path))

    assert isinstance(verifier, CommandCompletionVerifier)
    assert verifier.commands == ("python -m unittest",)


def test_eval_assertions_cover_files_metrics_trace_and_diff(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    run_dir = tmp_path / "run"
    artifacts = run_dir / "artifacts"
    workspace.mkdir()
    artifacts.mkdir(parents=True)
    (workspace / "README.md").write_text("hello eval\n", encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        '{"event":"artifact_written"}\n'
        '{"event":"policy_decision","decision_type":"deny","policy_name":"CommandPolicy"}\n',
        encoding="utf-8",
    )
    (artifacts / "diff.patch").write_text("+++ b/README.md\n", encoding="utf-8")

    results = run_assertions(
        [
            {"type": "file_exists", "path": "README.md"},
            {"type": "file_not_exists", "path": "missing.txt"},
            {"type": "file_contains", "path": "README.md", "patterns": ["eval"]},
            {"type": "metric_at_least", "name": "turns", "value": 2},
            {"type": "metric_equals", "name": "turns", "value": 3},
            {"type": "run_status", "value": "waiting_approval"},
            {"type": "trace_contains_event", "event_type": "artifact_written"},
            {
                "type": "trace_contains_event",
                "event_type": "policy_decision",
                "fields": {"decision_type": "deny", "policy_name": "CommandPolicy"},
            },
            {"type": "diff_allowlist", "paths": ["README.md"]},
            {"type": "diff_does_not_delete", "paths": ["src/", "tests/"]},
        ],
        workspace_dir=workspace,
        run_dir=run_dir,
        metrics={"turns": 3, "status": "waiting_approval"},
    )

    assert all(result.passed for result in results)


def test_metric_equals_rejects_a_different_value(tmp_path) -> None:
    result = assertions.run_assertion(
        {"type": "metric_equals", "name": "turns", "value": 9},
        workspace_dir=tmp_path,
        run_dir=tmp_path,
        metrics={"turns": 8},
    )

    assert result.passed is False
    assert "expected exactly 9.0" in result.message


def test_metric_equals_rejects_a_missing_zero_valued_field(tmp_path) -> None:
    result = assertions.run_assertion(
        {"type": "metric_equals", "name": "repeated_file_reads", "value": 0},
        workspace_dir=tmp_path,
        run_dir=tmp_path,
        metrics={},
    )

    assert result.passed is False
    assert "field_present=False" in result.message


def test_trace_action_sequence_accepts_contiguous_independent_commands(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    commands = [
        "python -m unittest discover -s tests",
        "cat VALIDATION_CONTRACT.md",
        "cat tests/test_validator.py",
        "cat src/validator.py",
        "python -m unittest tests.test_validator -v",
    ]
    _write_action_trace(run_dir, commands)

    result = assertions.run_assertion(
        {
            "type": "trace_action_sequence",
            "start_index": 2,
            "commands": [
                "cat VALIDATION_CONTRACT.md",
                "cat tests/test_validator.py",
                "cat src/validator.py",
            ],
        },
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is True


def test_trace_action_sequence_rejects_right_commands_at_wrong_position(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_action_trace(
        run_dir,
        [
            "cat VALIDATION_CONTRACT.md",
            "cat tests/test_validator.py",
            "cat src/validator.py",
        ],
    )

    result = assertions.run_assertion(
        {
            "type": "trace_action_sequence",
            "start_index": 3,
            "commands": [
                "cat VALIDATION_CONTRACT.md",
                "cat tests/test_validator.py",
                "cat src/validator.py",
            ],
        },
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is False


@pytest.mark.parametrize(
    "commands",
    [
        ["cat VALIDATION_CONTRACT.md", "cat src/validator.py"],
        [
            "cat tests/test_validator.py",
            "cat VALIDATION_CONTRACT.md",
            "cat src/validator.py",
        ],
        [
            "cat VALIDATION_CONTRACT.md && cat tests/test_validator.py",
            "cat src/validator.py",
        ],
        [
            "cat\nVALIDATION_CONTRACT.md",
            "cat tests/test_validator.py",
            "cat src/validator.py",
        ],
    ],
    ids=["missing-test-read", "wrong-order", "combined-read", "newline-split-read"],
)
def test_trace_action_sequence_rejects_incomplete_or_non_independent_reads(
    tmp_path,
    commands,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_action_trace(run_dir, commands)

    result = assertions.run_assertion(
        {
            "type": "trace_action_sequence",
            "commands": [
                "cat VALIDATION_CONTRACT.md",
                "cat tests/test_validator.py",
                "cat src/validator.py",
            ],
        },
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is False


def test_trace_action_shape_locks_exact_and_regex_commands(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    actions = [
        {"command": "python -m unittest discover -s tests"},
        {
            "heredoc_write": {
                "path": "src/validator.py",
                "delimiter": "EOF",
            }
        },
    ]
    _write_action_trace(
        run_dir,
        [
            "python -m unittest discover -s tests",
            "cat > src/validator.py << 'EOF'\ndef repaired():\n    return True\nEOF",
        ],
    )

    result = assertions.run_assertion(
        {"type": "trace_action_shape", "actions": actions},
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is True
    assert result.spec_sha256 == assertions.assertion_spec_sha256(
        {"type": "trace_action_shape", "actions": actions}
    )


def test_trace_action_shape_binds_exit_code_and_artifact_to_action(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    actions = [
        {
            "command": "python -m unittest discover -s tests",
            "expect_exit_code": 1,
            "artifact_ids": ["art_0001"],
        },
    ]
    _write_action_trace(
        run_dir,
        ["python -m unittest discover -s tests"],
        exit_codes=[1],
        artifact_ids=[["art_0001"]],
    )

    result = assertions.run_assertion(
        {"type": "trace_action_shape", "actions": actions},
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is True


def test_trace_action_shape_rejects_wrong_exit_code_or_unbound_artifact(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    actions = [
        {
            "command": "python -m unittest discover -s tests",
            "expect_exit_code": 1,
            "artifact_ids": ["art_0001"],
        },
    ]
    _write_action_trace(
        run_dir,
        ["python -m unittest discover -s tests"],
        exit_codes=[0],
    )

    result = assertions.run_assertion(
        {"type": "trace_action_shape", "actions": actions},
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is False


@pytest.mark.parametrize(
    "commands",
    [
        [
            "python -m unittest discover -s tests",
            (
                "cat > src/validator.py << 'EOF'\n"
                "def repaired():\n"
                "    return True\n"
                "EOF\n"
                "python -m unittest tests.test_validator -v"
            ),
        ],
        [
            "python -m unittest discover -s tests",
            (
                "cat > src/validator.py << 'EOF'\n"
                "def repaired():\n"
                "    return True\n"
                "EOF\n"
                "cat VALIDATION_CONTRACT.md\n"
                "EOF"
            ),
        ],
        [
            "python -m unittest discover -s tests",
            "cat > src/validator.py << 'EOF'\ndef repaired():\n    return True\nEOF",
            "echo extra-action",
        ],
    ],
    ids=[
        "trailing-command-after-heredoc",
        "early-delimiter-with-hidden-command",
        "extra-bash-action",
    ],
)
def test_trace_action_shape_rejects_unlocked_or_extra_actions(
    tmp_path,
    commands,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_action_trace(run_dir, commands)
    actions = [
        {"command": "python -m unittest discover -s tests"},
        {
            "heredoc_write": {
                "path": "src/validator.py",
                "delimiter": "EOF",
            }
        },
    ]

    result = assertions.run_assertion(
        {"type": "trace_action_shape", "actions": actions},
        workspace_dir=tmp_path,
        run_dir=run_dir,
        metrics={},
    )

    assert result.passed is False


def _write_action_trace(
    run_dir,
    commands,
    *,
    exit_codes=None,
    artifact_ids=None,
) -> None:
    resolved_exit_codes = exit_codes or [0] * len(commands)
    resolved_artifact_ids = artifact_ids or [[] for _ in commands]
    assert len(resolved_exit_codes) == len(commands)
    assert len(resolved_artifact_ids) == len(commands)
    events = []
    for command, exit_code, action_artifacts in zip(
        commands,
        resolved_exit_codes,
        resolved_artifact_ids,
        strict=True,
    ):
        events.append(
            {
                "event": "action_parsed",
                "action": {"type": "bash", "command": command},
            }
        )
        events.append(
            {
                "event": "sandbox_exec_finished",
                "observation": {
                    "exit_code": exit_code,
                    "artifact_ids": action_artifacts,
                },
            }
        )
        events.extend(
            {
                "event": "artifact_written",
                "artifact_id": artifact_id,
            }
            for artifact_id in action_artifacts
        )
    (run_dir / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def test_eval_runner_writes_reports(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "README.md").write_text("ok\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        """
name: demo
prompt: Finish.
assertions:
  - type: file_contains
    path: README.md
    patterns: ["ok"]
""",
        encoding="utf-8",
    )

    def fake_agent_runner(case, state: RunState) -> RunState:
        state.status = "completed"
        state.metrics["turns"] = 1
        return state

    result = run_eval_suite(tmp_path / "cases", runs_root=tmp_path / "runs", agent_runner=fake_agent_runner)
    json_path, markdown_path = write_eval_report(result, tmp_path / "reports")

    assert result.passed is True
    assert result.repeat == 1
    assert json.loads(json_path.read_text(encoding="utf-8"))["passed"] is True
    assert "Overall: PASS" in markdown_path.read_text(encoding="utf-8")
    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert report_data["case_summary"][0]["pass_rate"] == 1.0
    assert report_data["case_summary"][0]["diff_paths"] == []
    assert report_data["schema_version"] == 2
    assert report_data["suite_id"] == result.suite_id
    run_dir = tmp_path / "runs" / result.cases[0].run_id
    case_result = json.loads(
        (run_dir / "eval_result.json").read_text(encoding="utf-8")
    )
    assert case_result["passed"] is True
    assert case_result["run_status"] == "completed"
    assert case_result["task_success"] is True
    assert case_result["agent_success"] is True
    assert case_result["infrastructure_success"] is True
    assert (run_dir / "eval_result.md").exists()
    assert (tmp_path / "reports" / "report.csv").exists()
    assert (tmp_path / "reports" / "manifest.json").exists()


def test_eval_repeat_preserves_independent_run_evidence(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: demo
capability: debugging
prompt: Finish.
assertions: []
""",
        encoding="utf-8",
    )

    def fake_agent_runner(case, state: RunState) -> RunState:
        state.status = "completed"
        state.metrics["status"] = state.status
        return state

    result = run_eval_suite(
        tmp_path / "cases",
        runs_root=tmp_path / "runs",
        agent_runner=fake_agent_runner,
        repeat=3,
        configuration={"model": "fixed-model", "temperature": 0},
    )

    assert result.passed is True
    assert result.repeat == 3
    assert [case.attempt for case in result.cases] == [1, 2, 3]
    assert len({case.run_id for case in result.cases}) == 3
    assert all((tmp_path / "runs" / case.run_id / "eval_result.json").exists() for case in result.cases)
    assert result.configuration == {"model": "fixed-model", "temperature": 0}


def test_two_eval_suites_keep_distinct_manifests_reports_and_run_pointers(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        "name: demo\nprompt: Finish.\nassertions: []\n",
        encoding="utf-8",
    )

    def fake_agent_runner(case, state: RunState) -> RunState:
        state.status = "completed"
        return state

    results = [
        run_eval_suite(
            tmp_path / "cases",
            runs_root=tmp_path / ".minicc" / "runs",
            agent_runner=fake_agent_runner,
            configuration={"model": "fixed", "temperature": 0},
            milestone="stable-v2.0.2",
            stage="development_precheck",
        )
        for _ in range(2)
    ]
    bundles = [
        write_suite_report(result, tmp_path / ".minicc" / "suites")
        for result in results
    ]

    assert results[0].suite_id != results[1].suite_id
    assert results[0].cases[0].run_id != results[1].cases[0].run_id
    assert all(bundle.manifest_path.exists() for bundle in bundles)
    assert all(bundle.report_json_path.exists() for bundle in bundles)
    for result, bundle in zip(results, bundles, strict=True):
        manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
        report = json.loads(bundle.report_json_path.read_text(encoding="utf-8"))
        run_result = json.loads(
            (tmp_path / ".minicc" / "runs" / result.cases[0].run_id / "eval_result.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["suite_id"] == result.suite_id
        assert manifest["runs"][0]["run_id"] == result.cases[0].run_id
        assert report["created_at"] == result.created_at
        assert report["completed_at"] is None
        assert run_result["suite_id"] == result.suite_id
        assert run_result["evidence"]["suite_manifest"] == str(bundle.manifest_path.resolve())
        assert run_result["schema_version"] == 2
        assert run_result["task_success"] is True
        assert run_result["agent_success"] is True
        assert run_result["infrastructure_success"] is True
        assert run_result["policy_outcome"] == "clear"


def test_eval_suite_filters_named_cases_and_rejects_unknown_names(tmp_path) -> None:
    for name in ["C01", "C02"]:
        case_dir = tmp_path / "cases" / name
        (case_dir / "fixture").mkdir(parents=True)
        (case_dir / "case.yaml").write_text(
            f"name: {name}\nprompt: Finish.\nassertions: []\n",
            encoding="utf-8",
        )

    def fake_agent_runner(case, state: RunState) -> RunState:
        state.status = "completed"
        return state

    result = run_eval_suite(
        tmp_path / "cases",
        runs_root=tmp_path / "runs",
        agent_runner=fake_agent_runner,
        case_names=["C02"],
    )

    assert [case.name for case in result.cases] == ["C02"]
    with pytest.raises(ValueError, match="Unknown eval case"):
        run_eval_suite(
            tmp_path / "cases",
            runs_root=tmp_path / "runs",
            agent_runner=fake_agent_runner,
            case_names=["missing"],
        )


def test_aggregate_case_results_reports_metrics_and_diff_paths(tmp_path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "diff.patch").write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "diff --git a/tests/test_app.py b/tests/test_app.py\n",
        encoding="utf-8",
    )
    case = type(
        "CaseResult",
        (),
        {
            "name": "C01",
            "capability": "repo_understanding",
            "passed": True,
            "run_status": "completed",
            "attempt": 1,
            "run_dir": str(run_dir),
            "metrics": {"turns": 4, "bash_actions": 2, "total_duration_ms": 1000},
            "assertions": [],
        },
    )()

    summary = aggregate_case_results([case])[0]

    assert summary["pass_rate"] == 1.0
    assert summary["average_turns"] == 4
    assert summary["average_bash_actions"] == 2
    assert summary["average_duration_ms"] == 1000
    assert summary["diff_paths"] == ["src/app.py", "tests/test_app.py"]


def test_eval_runner_rejects_waiting_approval_for_ordinary_case(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "ordinary"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: ordinary
prompt: Finish without approval.
assertions:
  - type: run_status
    value: waiting_approval
""",
        encoding="utf-8",
    )

    def waiting_agent(case, state: RunState) -> RunState:
        state.status = "waiting_approval"
        return state

    result = run_eval_suite(tmp_path / "cases", runs_root=tmp_path / "runs", agent_runner=waiting_agent)

    assert result.passed is False
    assert result.cases[0].passed is False
    assert result.cases[0].task_success is True
    assert result.cases[0].agent_success is False
    assert result.cases[0].infrastructure_success is True


def test_eval_runner_allows_explicit_hitl_waiting_status(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "hitl"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: hitl
capability: hitl_safety
prompt: Request approval.
assertions:
  - type: run_status
    value: waiting_approval
""",
        encoding="utf-8",
    )

    def waiting_agent(case, state: RunState) -> RunState:
        state.status = "waiting_approval"
        state.metrics["status"] = state.status
        return state

    result = run_eval_suite(tmp_path / "cases", runs_root=tmp_path / "runs", agent_runner=waiting_agent)

    assert result.passed is True
    assert result.cases[0].passed is True


def test_eval_runner_classifies_infrastructure_failure(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "broken"
    (case_dir / "fixture").mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        "name: broken\nprompt: Finish.\nassertions: []\n",
        encoding="utf-8",
    )

    def broken_agent(case, state):
        raise RuntimeError("docker unavailable")

    result = run_eval_suite(
        tmp_path / "cases",
        runs_root=tmp_path / "runs",
        agent_runner=broken_agent,
    )

    assert result.passed is False
    assert result.cases[0].task_success is True
    assert result.cases[0].agent_success is False
    assert result.cases[0].infrastructure_success is False


def test_eval_case_budget_overrides_settings(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: demo
prompt: Finish.
budget:
  max_turns: 3
  max_bash_actions: 4
  max_action_timeout_sec: 5
context:
  max_prompt_chars: 2000
  recent_turns: 2
  artifact_preview_chars: 1500
  summary_max_chars: 500
  field_preview_chars: 750
  retention_markers: [src/app.py]
""",
        encoding="utf-8",
    )
    settings = Settings(
        provider=ProviderSettings(),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(max_turns=10, max_bash_actions=20, max_action_timeout_sec=30),
        context=ContextSettings(),
        policy=PolicySettings(),
    )

    adjusted = _settings_for_eval_case(settings, load_case(case_dir / "case.yaml"))

    assert adjusted.budget.max_turns == 3
    assert adjusted.budget.max_bash_actions == 4
    assert adjusted.budget.max_action_timeout_sec == 5
    assert adjusted.context.max_prompt_chars == 2000
    assert adjusted.context.recent_turns == 2
    assert adjusted.context.artifact_preview_chars == 1500
    assert adjusted.context.summary_max_chars == 500
    assert adjusted.context.field_preview_chars == 750
    assert adjusted.context.retention_markers == ("src/app.py",)


def test_ordinary_eval_denies_network_instead_of_waiting_for_approval(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "ordinary"
    (case_dir / "fixture").mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        "name: ordinary\ncapability: debugging\nprompt: Fix it.\nassertions: []\n",
        encoding="utf-8",
    )
    settings = Settings(
        provider=ProviderSettings(),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(),
        context=ContextSettings(),
        policy=PolicySettings(require_approval_for_network=True),
    )

    adjusted = _settings_for_eval_case(settings, load_case(case_dir / "case.yaml"))

    assert adjusted.policy.require_approval_for_network is False


def test_case_constraints_reuse_verifier_commands(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "debug"
    (case_dir / "fixture").mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: debug
prompt: Fix it.
workspace:
  writable_paths: ["src/"]
assertions:
  - type: command
    command: python -m unittest discover -s tests
""",
        encoding="utf-8",
    )

    constraints = _case_constraints(load_case(case_dir / "case.yaml"))

    assert any("read-only workspace" in item for item in constraints)
    assert any("python -m unittest discover -s tests" in item for item in constraints)
    assert any("do not install" in item for item in constraints)


def test_windows_host_bash_command_normalizes_python(monkeypatch) -> None:
    monkeypatch.setattr(assertions.sys, "platform", "win32")

    assert assertions._normalize_command_for_host_bash(
        "python -m unittest discover -s tests | python -m json.tool"
    ) == "python3 -m unittest discover -s tests | python3 -m json.tool"


def test_command_assertion_decodes_windows_output_as_utf8(monkeypatch, tmp_path) -> None:
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return Completed()

    monkeypatch.setattr(assertions.subprocess, "run", fake_run)

    result = assertions._assert_command({"command": "python -V"}, tmp_path)

    assert result.passed is True
    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


def test_infrastructure_error_includes_subprocess_stderr() -> None:
    error = subprocess.CalledProcessError(125, ["docker", "run"], stderr="mount failed")

    message = _format_infrastructure_error(error)

    assert "CalledProcessError" in message
    assert "mount failed" in message
