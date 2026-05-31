from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote


def serve_trace_viewer(*, runs_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    handler = _handler_for(runs_root)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"miniCC trace viewer: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTrace viewer stopped.")
    finally:
        server.server_close()


def _handler_for(runs_root: Path) -> type[BaseHTTPRequestHandler]:
    class TraceViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = unquote(self.path.split("?", 1)[0])
            if path == "/":
                self._send_html(render_index(runs_root))
                return
            if path == "/runs":
                self._send_json(list_runs(runs_root))
                return
            if path.startswith("/runs/") and path.endswith("/trace"):
                run_id = path.removeprefix("/runs/").removesuffix("/trace").strip("/")
                self._send_json(read_trace(runs_root, run_id))
                return
            if path.startswith("/runs/") and path.endswith("/metrics"):
                run_id = path.removeprefix("/runs/").removesuffix("/metrics").strip("/")
                self._send_json(read_json(runs_root / run_id / "metrics.json"))
                return
            if path.startswith("/runs/") and path.endswith("/diff"):
                run_id = path.removeprefix("/runs/").removesuffix("/diff").strip("/")
                self._send_text((runs_root / run_id / "artifacts" / "diff.patch").read_text(
                    encoding="utf-8",
                    errors="replace",
                ) if (runs_root / run_id / "artifacts" / "diff.patch").exists() else "")
                return
            self.send_error(404, "Not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, content: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        def _send_json(self, payload: Any) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

        def _send_text(self, content: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

    return TraceViewerHandler


def list_runs(runs_root: Path) -> list[dict[str, Any]]:
    if not runs_root.exists():
        return []
    runs: list[dict[str, Any]] = []
    for run_dir in sorted((item for item in runs_root.iterdir() if item.is_dir()), reverse=True):
        if run_dir.name == "eval_reports":
            continue
        metrics = read_json(run_dir / "metrics.json")
        state = read_json(run_dir / "state.json")
        runs.append(
            {
                "run_id": run_dir.name,
                "status": metrics.get("status") or state.get("status"),
                "goal": state.get("goal", ""),
                "turns": metrics.get("turns", 0),
                "bash_actions": metrics.get("bash_actions", 0),
                "trace_path": str(run_dir / "trace.jsonl"),
                "metrics_path": str(run_dir / "metrics.json"),
            }
        )
    return runs


def read_trace(runs_root: Path, run_id: str) -> list[dict[str, Any]]:
    trace_path = runs_root / run_id / "trace.jsonl"
    if not trace_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def render_index(runs_root: Path) -> str:
    runs = list_runs(runs_root)
    rows = "\n".join(_render_run_row(run) for run in runs) or "<tr><td colspan='5'>No runs found.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>miniCC Trace Viewer</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border-bottom: 1px solid #d7dde5; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f6f8; }}
    pre {{ background: #f7f8fa; border: 1px solid #d7dde5; padding: 12px; overflow: auto; }}
    button {{ cursor: pointer; }}
    .muted {{ color: #607080; }}
  </style>
</head>
<body>
  <h1>miniCC Trace Viewer</h1>
  <p class="muted">Read-only view over <code>{html.escape(str(runs_root))}</code>.</p>
  <table>
    <thead><tr><th>Run</th><th>Status</th><th>Goal</th><th>Turns</th><th>Actions</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>Timeline</h2>
  <pre id="timeline">Select a run.</pre>
  <h2>Metrics</h2>
  <pre id="metrics">Select a run.</pre>
  <h2>Diff</h2>
  <pre id="diff">Select a run.</pre>
  <script>
    async function loadRun(runId) {{
      const [trace, metrics, diff] = await Promise.all([
        fetch(`/runs/${{runId}}/trace`).then(r => r.json()),
        fetch(`/runs/${{runId}}/metrics`).then(r => r.json()),
        fetch(`/runs/${{runId}}/diff`).then(r => r.text())
      ]);
      document.getElementById('timeline').textContent = trace.map(formatEvent).join('\\n\\n') || 'No trace.';
      document.getElementById('metrics').textContent = JSON.stringify(metrics, null, 2);
      document.getElementById('diff').textContent = diff || 'No diff.';
    }}
    function formatEvent(event) {{
      const copy = Object.assign({{}}, event);
      const type = copy.event;
      delete copy.event;
      return type + '\\n' + JSON.stringify(copy, null, 2);
    }}
  </script>
</body>
</html>"""


def _render_run_row(run: dict[str, Any]) -> str:
    run_id = html.escape(str(run.get("run_id", "")))
    status = html.escape(str(run.get("status", "")))
    goal = html.escape(str(run.get("goal", "")))
    turns = html.escape(str(run.get("turns", 0)))
    actions = html.escape(str(run.get("bash_actions", 0)))
    return (
        "<tr>"
        f"<td><button onclick=\"loadRun('{run_id}')\">{run_id}</button></td>"
        f"<td>{status}</td>"
        f"<td>{goal}</td>"
        f"<td>{turns}</td>"
        f"<td>{actions}</td>"
        "</tr>"
    )
