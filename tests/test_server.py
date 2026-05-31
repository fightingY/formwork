import json

from minicc.server.app import list_runs, read_trace, render_index


def test_trace_viewer_reads_runs_and_trace(tmp_path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "state.json").write_text('{"goal":"Run tests","status":"completed"}', encoding="utf-8")
    (run_dir / "metrics.json").write_text('{"status":"completed","turns":2,"bash_actions":1}', encoding="utf-8")
    (run_dir / "trace.jsonl").write_text('{"event":"run_started"}\n', encoding="utf-8")

    runs = list_runs(tmp_path)
    trace = read_trace(tmp_path, "run-1")
    html = render_index(tmp_path)

    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["goal"] == "Run tests"
    assert trace == [{"event": "run_started"}]
    assert "miniCC Trace Viewer" in html
    assert "run-1" in html
