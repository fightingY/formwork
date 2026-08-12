from minicc.core.run_catalog import RunCatalog
from minicc.server.app import (
    list_runs,
    list_versions,
    read_run_metrics,
    read_trace,
    render_index,
    summarize_trace,
)


def test_trace_viewer_reads_runs_and_trace(tmp_path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "state.json").write_text('{"goal":"Run tests","status":"completed"}', encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        '{"status":"completed","turns":2,"bash_actions":1,"artifact_bytes":12}',
        encoding="utf-8",
    )
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text('{"event":"run_started"}\n{"event":"run_completed"}\n', encoding="utf-8")

    runs = list_runs(tmp_path)
    trace = read_trace(tmp_path, "run-1")
    trace_summary = summarize_trace(trace_path)
    html = render_index(tmp_path)

    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["goal"] == "Run tests"
    assert runs[0]["event_count"] == 2
    assert runs[0]["last_event"] == "run_completed"
    assert runs[0]["artifact_bytes"] == 12
    assert trace == [{"event": "run_started"}, {"event": "run_completed"}]
    assert trace_summary == {"event_count": 2, "last_event": "run_completed"}
    assert "miniCC Trace Viewer" in html
    assert "Live refresh on" in html
    assert "eventTypeFilter" in html


def test_trace_viewer_rejects_unsafe_run_ids(tmp_path) -> None:
    assert read_trace(tmp_path, "../outside") == []


def test_trace_viewer_filters_and_orders_runs_by_version(tmp_path) -> None:
    runs_root = tmp_path / "runs"
    versions_root = tmp_path / "versions"
    for run_id, started_at in [("old-run", "2026-07-15T10:00:00"), ("new-run", "2026-07-16T10:00:00")]:
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text('{"goal":"demo","status":"completed"}', encoding="utf-8")
        (run_dir / "metrics.json").write_text(
            '{"status":"completed","started_at":"' + started_at + '"}',
            encoding="utf-8",
        )
        RunCatalog(versions_root).upsert(
            "stable-v2.0",
            {
                "schema_version": 2,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "stage": "formal_acceptance",
                "status": "completed",
                "result": "PASS",
                "started_at": started_at,
            },
        )

    runs = list_runs(runs_root, versions_root=versions_root, milestone="stable-v2.0")
    versions = list_versions(versions_root, current_milestone="stable-v2.0")
    html = render_index(runs_root, versions_root=versions_root, current_milestone="stable-v2.0")

    assert [run["run_id"] for run in runs] == ["new-run", "old-run"]
    assert runs[0]["title"] == "[V2.0][正式验收][RUN][PASS]"
    assert versions == [
        {
            "milestone": "stable-v2.0",
            "entry_count": 2,
            "updated_at": versions[0]["updated_at"],
            "is_current": True,
        }
    ]
    assert "versionFilter" in html
    assert "stable-v2.0" in html


def test_trace_viewer_corrects_historical_cache_rate_from_trace(tmp_path) -> None:
    run_dir = tmp_path / "run-cache"
    run_dir.mkdir()
    (run_dir / "metrics.json").write_text(
        '{"prompt_cache_hit_tokens":250,"prompt_cache_miss_tokens":850,"cache_hit_rate":0}',
        encoding="utf-8",
    )
    (run_dir / "trace.jsonl").write_text(
        "\n".join(
            [
                '{"event":"model_response","usage":{"prompt_tokens":1000,"cached_tokens":250,"cache_hit_tokens":250,"cache_miss_tokens":750}}',
                '{"event":"model_response","usage":{"prompt_tokens":100,"cached_tokens":0,"cache_hit_tokens":0,"cache_miss_tokens":100}}',
            ]
        ),
        encoding="utf-8",
    )

    metrics = read_run_metrics(run_dir)

    assert metrics["cache_metrics_available"] is True
    assert metrics["cache_observed_hit_tokens"] == 250
    assert metrics["cache_observed_prompt_tokens"] == 1100
    assert metrics["cache_hit_rate"] == 250 / 1100


def test_trace_viewer_filters_record_stage_and_degrades_missing_artifacts(tmp_path) -> None:
    runs_root = tmp_path / "runs"
    versions_root = tmp_path / "versions"
    for run_id, stage in [("formal", "formal_acceptance"), ("dev", "development_precheck")]:
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            '{"schema_version":2,"goal":"demo","status":"completed"}',
            encoding="utf-8",
        )
        RunCatalog(versions_root).upsert(
            "stable-v2.0.2",
            {
                "schema_version": 2,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "stage": stage,
                "status": "completed",
                "result": "PASS",
            },
        )

    formal = list_runs(
        runs_root,
        versions_root=versions_root,
        milestone="stable-v2.0.2",
        record_view="formal",
    )
    development = list_runs(
        runs_root,
        versions_root=versions_root,
        milestone="stable-v2.0.2",
        record_view="development",
    )

    assert [run["run_id"] for run in formal] == ["formal"]
    assert [run["run_id"] for run in development] == ["dev"]
    assert formal[0]["trace_available"] is False
    assert formal[0]["metrics_available"] is False
    assert formal[0]["diff_available"] is False
    assert "recordViewFilter" in render_index(
        runs_root,
        versions_root=versions_root,
        current_milestone="stable-v2.0.2",
    )
