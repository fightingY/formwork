import json

from minicc.core.state import RunState
from minicc.evals.assertions import run_assertions
from minicc.evals.case import discover_cases, load_case
from minicc.evals.runner import run_eval_suite, write_eval_report
from minicc.cli import _settings_for_eval_case
from minicc.config import BudgetSettings, ContextSettings, PolicySettings, ProviderSettings, SandboxSettings, Settings
from minicc.evals import assertions


def test_load_and_discover_eval_cases(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(
        """
name: demo
capability: repo_understanding
prompt: Write docs.
assertions:
  - type: file_exists
    path: README.md
""",
        encoding="utf-8",
    )

    case = load_case(case_dir / "case.yaml")

    assert case.name == "demo"
    assert case.fixture_dir == fixture.resolve()
    assert discover_cases(tmp_path / "cases") == [case]


def test_eval_assertions_cover_files_metrics_trace_and_diff(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    run_dir = tmp_path / "run"
    artifacts = run_dir / "artifacts"
    workspace.mkdir()
    artifacts.mkdir(parents=True)
    (workspace / "README.md").write_text("hello eval\n", encoding="utf-8")
    (run_dir / "trace.jsonl").write_text('{"event":"artifact_written"}\n', encoding="utf-8")
    (artifacts / "diff.patch").write_text("+++ b/README.md\n", encoding="utf-8")

    results = run_assertions(
        [
            {"type": "file_exists", "path": "README.md"},
            {"type": "file_not_exists", "path": "missing.txt"},
            {"type": "file_contains", "path": "README.md", "patterns": ["eval"]},
            {"type": "metric_at_least", "name": "turns", "value": 2},
            {"type": "run_status", "value": "waiting_approval"},
            {"type": "trace_contains_event", "event_type": "artifact_written"},
            {"type": "diff_allowlist", "paths": ["README.md"]},
            {"type": "diff_does_not_delete", "paths": ["src/", "tests/"]},
        ],
        workspace_dir=workspace,
        run_dir=run_dir,
        metrics={"turns": 3, "status": "waiting_approval"},
    )

    assert all(result.passed for result in results)


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
    assert json.loads(json_path.read_text(encoding="utf-8"))["passed"] is True
    assert "Overall: PASS" in markdown_path.read_text(encoding="utf-8")


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


def test_windows_host_bash_command_normalizes_python(monkeypatch) -> None:
    monkeypatch.setattr(assertions.sys, "platform", "win32")

    assert assertions._normalize_command_for_host_bash(
        "python -m unittest discover -s tests | python -m json.tool"
    ) == "python3 -m unittest discover -s tests | python3 -m json.tool"
