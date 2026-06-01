from minicc.server.app import list_runs, read_trace, render_index, summarize_trace


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
