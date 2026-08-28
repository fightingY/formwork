import queue
import time
from pathlib import Path

import pytest

from minicc.server.ops import (
    Job,
    JobRegistry,
    OpsBroker,
    _eval_namespace,
    _existing_run_ids,
    _is_safe_run_id,
    _replay_run_namespace,
    _run_namespace,
    job_to_dict,
    launch_job,
    launch_resume_like_job,
    render_ops_index,
)


def test_ops_index_renders_single_page_with_tabs() -> None:
    html = render_ops_index()
    assert "miniCC Ops Console" in html
    assert "/api/runs" in html
    assert "/api/eval" in html
    assert "/api/replay" in html
    assert "EventSource" in html
    assert "runApproveBtn" in html


def test_safety_guard_rejects_path_traversal() -> None:
    assert _is_safe_run_id("run-123") is True
    assert _is_safe_run_id("../outside") is False
    assert _is_safe_run_id("a/b") is False
    assert _is_safe_run_id("") is False
    assert _is_safe_run_id(".") is False
    assert _is_safe_run_id("..") is False


def test_job_registry_create_get_list() -> None:
    registry = JobRegistry()
    job = registry.create("runs", {"goal": "do it"}, execute_local=True)

    assert registry.get(job.job_id) is job
    assert registry.get("missing") is None
    assert registry.list("runs") == [job]
    assert registry.list("eval") == []


def test_job_to_dict_shape() -> None:
    job = Job(job_id="j1", kind="eval", request={"path": "eval_cases"})
    job.run_ids.append("run-1")
    job.status = "completed"

    payload = job_to_dict(job)

    assert payload == {
        "job_id": "j1",
        "kind": "eval",
        "status": "completed",
        "run_ids": ["run-1"],
        "error": None,
        "created_at": job.created_at,
        "request": {"path": "eval_cases"},
    }


def test_broker_fans_out_and_unsubscribes() -> None:
    broker = OpsBroker()
    sub = broker.subscribe("job-1")
    broker.publish("job-1", {"type": "job_status", "status": "completed"})
    assert sub.get(timeout=1) == {"type": "job_status", "status": "completed"}

    broker.unsubscribe("job-1", sub)
    broker.publish("job-1", {"type": "ignored"})
    with pytest.raises(queue.Empty):
        sub.get(timeout=0.05)


def test_run_namespace_builds_expected_args() -> None:
    ns = _run_namespace(
        {
            "goal": "  fix the bug  ",
            "milestone": "v4.1",
            "verify_command": ["pytest -q", ""],
            "execute_local": False,
        }
    )

    assert ns.goal == "fix the bug"
    assert ns.milestone == "v4.1"
    assert ns.verify_command == ["pytest -q"]
    assert ns.execute_local is False
    assert ns.source_dir is None
    assert ns.no_workspace_copy is False
    assert ns.verification_timeout_sec == 120


def test_eval_namespace_hardcodes_excluded_fields() -> None:
    ns = _eval_namespace(
        {
            "path": "eval_cases/real_project_suite_v1",
            "repeat": 3,
            "case_names": ["case_a", "case_b"],
            "execute_local": True,
        }
    )

    assert ns.path == "eval_cases/real_project_suite_v1"
    assert ns.repeat == 3
    assert ns.case_names == ["case_a", "case_b"]
    assert ns.execute_local is True
    # Internal experiment/release-gate knobs must stay off the web surface.
    assert ns.release_gate is False
    assert ns.context_variant is None
    assert ns.cache_variant is None
    assert ns.guidance_variant is None


def test_replay_run_namespace_builds_expected_args(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    ns = _replay_run_namespace({"case": str(case_dir), "fresh": True, "execute_local": False})

    assert ns.case == case_dir
    assert ns.fresh is True
    assert ns.execute_local is False
    assert ns.json_output is False


def test_existing_run_ids_scans_state_json(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    (runs_root / "run-a").mkdir()
    (runs_root / "run-a" / "state.json").write_text("{}", encoding="utf-8")
    (runs_root / "run-b").mkdir()  # no state.json yet -- not a real run dir

    assert _existing_run_ids(runs_root) == {"run-a"}
    assert _existing_run_ids(tmp_path / "missing") == set()


def _wait_for_status(job: Job, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in {"completed", "failed"}:
            return
        time.sleep(0.05)
    raise AssertionError(f"job did not reach a terminal status in time: {job.status}")


def test_launch_job_runs_command_in_background_and_completes(tmp_path: Path) -> None:
    registry = JobRegistry()
    broker = OpsBroker()
    calls: list[object] = []

    def fake_command(namespace: object) -> int:
        calls.append(namespace)
        return 0

    job = launch_job(
        registry,
        broker,
        "runs",
        {"goal": "demo"},
        fake_command,
        _run_namespace,
        runs_root=tmp_path / "runs",
    )

    _wait_for_status(job)

    assert job.status == "completed"
    assert job.error is None
    assert len(calls) == 1
    assert calls[0].goal == "demo"


def test_launch_job_records_error_on_nonzero_exit(tmp_path: Path) -> None:
    registry = JobRegistry()
    broker = OpsBroker()

    job = launch_job(
        registry,
        broker,
        "eval",
        {"path": "eval_cases"},
        lambda namespace: 2,
        _eval_namespace,
        runs_root=tmp_path / "runs",
    )

    _wait_for_status(job)

    assert job.status == "failed"
    assert job.error is not None and "status 2" in job.error


def test_launch_resume_like_job_seeds_known_run_id_and_skips_prior_trace(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "run-known"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text('{"type": "old_event"}\n', encoding="utf-8")

    registry = JobRegistry()
    broker = OpsBroker()
    subscriber = broker.subscribe("placeholder")  # ensure broker API usable before job id known

    job = launch_resume_like_job(
        registry,
        broker,
        "runs",
        {"run_id": "run-known", "verb": "approve"},
        "run-known",
        lambda: 0,
        runs_root=runs_root,
    )
    broker.unsubscribe("placeholder", subscriber)

    events_sub = broker.subscribe(job.job_id)
    _wait_for_status(job)

    assert job.status == "completed"
    assert job.run_ids == ["run-known"]

    # Drain events; the pre-existing "old_event" line must never be replayed.
    seen_types = []
    while True:
        try:
            event = events_sub.get(timeout=1.0)
        except queue.Empty:
            break
        seen_types.append(event.get("type"))
        if event.get("type") == "job_status":
            break
    assert "trace_event" not in seen_types
