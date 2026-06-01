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
                if not _is_safe_run_id(run_id):
                    self.send_error(404, "Run not found")
                    return
                self._send_json(read_trace(runs_root, run_id))
                return
            if path.startswith("/runs/") and path.endswith("/metrics"):
                run_id = path.removeprefix("/runs/").removesuffix("/metrics").strip("/")
                if not _is_safe_run_id(run_id):
                    self.send_error(404, "Run not found")
                    return
                self._send_json(read_json(_run_dir(runs_root, run_id) / "metrics.json"))
                return
            if path.startswith("/runs/") and path.endswith("/diff"):
                run_id = path.removeprefix("/runs/").removesuffix("/diff").strip("/")
                if not _is_safe_run_id(run_id):
                    self.send_error(404, "Run not found")
                    return
                diff_path = _run_dir(runs_root, run_id) / "artifacts" / "diff.patch"
                self._send_text(diff_path.read_text(encoding="utf-8", errors="replace") if diff_path.exists() else "")
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
        trace_summary = summarize_trace(run_dir / "trace.jsonl")
        runs.append(
            {
                "run_id": run_dir.name,
                "status": metrics.get("status") or state.get("status"),
                "goal": state.get("goal", ""),
                "turns": metrics.get("turns", 0),
                "bash_actions": metrics.get("bash_actions", 0),
                "policy_denials": metrics.get("policy_denials", 0),
                "approvals_requested": metrics.get("approvals_requested", 0),
                "artifact_bytes": metrics.get("artifact_bytes", 0),
                "started_at": metrics.get("started_at"),
                "completed_at": metrics.get("completed_at"),
                "total_duration_ms": metrics.get("total_duration_ms"),
                "event_count": trace_summary["event_count"],
                "last_event": trace_summary["last_event"],
                "trace_path": str(run_dir / "trace.jsonl"),
                "metrics_path": str(run_dir / "metrics.json"),
            }
        )
    return runs


def read_trace(runs_root: Path, run_id: str) -> list[dict[str, Any]]:
    if not _is_safe_run_id(run_id):
        return []
    trace_path = _run_dir(runs_root, run_id) / "trace.jsonl"
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


def summarize_trace(trace_path: Path) -> dict[str, Any]:
    if not trace_path.exists():
        return {"event_count": 0, "last_event": ""}
    event_count = 0
    last_event = ""
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            event_count += 1
            last_event = str(event.get("event") or "")
    return {"event_count": event_count, "last_event": last_event}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_safe_run_id(run_id: str) -> bool:
    return bool(run_id) and "/" not in run_id and "\\" not in run_id and run_id not in {".", "..", "eval_reports"}


def _run_dir(runs_root: Path, run_id: str) -> Path:
    return runs_root / run_id


def render_index(runs_root: Path) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>miniCC Trace Viewer</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #18222f;
      --muted: #66717f;
      --line: #d8dee7;
      --line-strong: #b9c3d0;
      --accent: #0f766e;
      --accent-soft: #d9f0ed;
      --danger: #b42318;
      --warn: #a15c07;
      --code: #111827;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    button, input, select {{
      font: inherit;
    }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      cursor: pointer;
      border-radius: 6px;
      padding: 7px 10px;
    }}
    button:hover {{ border-color: var(--line-strong); }}
    button.active {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: #07534e;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      background: #fff;
      color: var(--ink);
    }}
    .app {{
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 100vh;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 12px;
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 280px;
      justify-content: flex-end;
    }}
    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent);
      display: inline-block;
      margin-right: 6px;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
      min-height: 0;
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--panel);
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
    }}
    .run-tools {{
      display: grid;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }}
    .run-list {{
      overflow: auto;
      padding: 8px;
    }}
    .run-item {{
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px;
      text-align: left;
      padding: 10px;
      margin-bottom: 8px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 8px;
    }}
    .run-item.active {{
      border-color: var(--accent);
      background: #f0fbf9;
    }}
    .run-title {{
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .run-goal {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: 12px;
      max-height: 36px;
      overflow: hidden;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border-radius: 999px;
      background: #eef1f5;
      color: #364152;
      font-size: 12px;
      white-space: nowrap;
    }}
    .pill.running {{ background: #d9f0ed; color: #07534e; }}
    .pill.waiting_approval {{ background: #fff2d6; color: var(--warn); }}
    .pill.failed {{ background: #fde2df; color: var(--danger); }}
    .content {{
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto auto 1fr;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(6, minmax(88px, 1fr));
      gap: 10px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      min-height: 64px;
      background: #fbfcfd;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
    }}
    .metric-value {{
      margin-top: 5px;
      font-size: 18px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .tabs {{
      display: flex;
      gap: 6px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    .tab-spacer {{ flex: 1; }}
    .filter-row {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 180px 150px;
      gap: 8px;
      width: min(680px, 100%);
    }}
    .panel {{
      min-height: 0;
      overflow: auto;
      padding: 12px;
    }}
    .event {{
      display: grid;
      grid-template-columns: 170px minmax(150px, 240px) 1fr;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
    }}
    .event-type {{
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .event-time {{
      color: var(--muted);
      font-size: 12px;
    }}
    .event-main {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}
    .event-preview {{
      color: #384454;
      white-space: pre-wrap;
    }}
    .event-json, .code-pane {{
      margin: 8px 0 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0f172a;
      color: #e5e7eb;
      overflow: auto;
      white-space: pre-wrap;
      max-height: 420px;
    }}
    .hidden {{ display: none; }}
    .empty {{
      color: var(--muted);
      padding: 24px;
      text-align: center;
    }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); max-height: 42vh; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .event {{ grid-template-columns: 1fr; }}
      header {{ align-items: flex-start; flex-direction: column; }}
      .header-actions {{ justify-content: flex-start; min-width: 0; width: 100%; }}
      .filter-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div>
        <h1>miniCC Trace Viewer</h1>
        <div class="subtle">Read-only over <code>{html.escape(str(runs_root))}</code></div>
      </div>
      <div class="header-actions">
        <span class="subtle"><span class="status-dot"></span><span id="refreshState">Live refresh on</span></span>
        <button id="toggleRefresh" type="button">Pause</button>
        <button id="refreshNow" type="button">Refresh</button>
      </div>
    </header>
    <main>
      <aside>
        <div class="run-tools">
          <input id="runSearch" type="search" placeholder="Search runs or goals">
          <select id="statusFilter" aria-label="Filter by status">
            <option value="">All statuses</option>
            <option value="running">running</option>
            <option value="waiting_approval">waiting_approval</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
          </select>
        </div>
        <div id="runList" class="run-list"><div class="empty">Loading runs.</div></div>
      </aside>
      <section class="content">
        <div id="summary" class="summary"></div>
        <div class="tabs">
          <button class="active" data-tab="timeline" type="button">Timeline</button>
          <button data-tab="metrics" type="button">Metrics</button>
          <button data-tab="diff" type="button">Diff</button>
          <div class="tab-spacer"></div>
          <div class="filter-row" id="timelineFilters">
            <input id="eventSearch" type="search" placeholder="Search events">
            <select id="eventTypeFilter" aria-label="Filter events by type">
              <option value="">All event types</option>
            </select>
            <select id="eventFocusFilter" aria-label="Focus events">
              <option value="">All events</option>
              <option value="policy">Policy & approvals</option>
              <option value="execution">Execution</option>
              <option value="model">Model</option>
              <option value="errors">Errors</option>
            </select>
          </div>
        </div>
        <div id="timelinePanel" class="panel"></div>
        <div id="metricsPanel" class="panel hidden"></div>
        <div id="diffPanel" class="panel hidden"></div>
      </section>
    </main>
  </div>
  <script>
    const state = {{
      runs: [],
      trace: [],
      metrics: {{}},
      diff: '',
      selectedRunId: '',
      activeTab: 'timeline',
      autoRefresh: true,
      intervalId: null
    }};

    const eventFocus = {{
      policy: new Set(['policy_decision', 'approval_requested']),
      execution: new Set(['sandbox_exec_started', 'sandbox_exec_finished', 'observation_created', 'artifact_written']),
      model: new Set(['prompt_built', 'model_response', 'action_parsed']),
      errors: new Set(['run_failed', 'context_compacted'])
    }};

    const $ = (id) => document.getElementById(id);

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }}[char]));
    }}

    function formatNumber(value) {{
      if (value === null || value === undefined || value === '') return '0';
      return Number(value).toLocaleString();
    }}

    function formatDuration(ms) {{
      const value = Number(ms || 0);
      if (!value) return '0s';
      if (value < 1000) return value + 'ms';
      return (value / 1000).toFixed(1) + 's';
    }}

    function shortTime(value) {{
      if (!value) return '';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleTimeString();
    }}

    async function fetchJson(url) {{
      const response = await fetch(url, {{ cache: 'no-store' }});
      if (!response.ok) throw new Error(`Request failed: ${{url}}`);
      return response.json();
    }}

    async function fetchText(url) {{
      const response = await fetch(url, {{ cache: 'no-store' }});
      if (!response.ok) throw new Error(`Request failed: ${{url}}`);
      return response.text();
    }}

    async function refreshRuns() {{
      state.runs = await fetchJson('/runs');
      if (!state.selectedRunId && state.runs.length) {{
        state.selectedRunId = state.runs[0].run_id;
      }}
      if (state.selectedRunId && !state.runs.some(run => run.run_id === state.selectedRunId)) {{
        state.selectedRunId = state.runs[0]?.run_id || '';
      }}
      renderRuns();
      if (state.selectedRunId) await loadRun(state.selectedRunId, false);
    }}

    async function loadRun(runId, renderList = true) {{
      state.selectedRunId = runId;
      const [trace, metrics, diff] = await Promise.all([
        fetchJson(`/runs/${{encodeURIComponent(runId)}}/trace`),
        fetchJson(`/runs/${{encodeURIComponent(runId)}}/metrics`),
        fetchText(`/runs/${{encodeURIComponent(runId)}}/diff`)
      ]);
      state.trace = trace;
      state.metrics = metrics;
      state.diff = diff;
      populateEventTypes();
      renderSummary();
      renderActivePanel();
      if (renderList) renderRuns();
    }}

    function filteredRuns() {{
      const query = $('runSearch').value.trim().toLowerCase();
      const status = $('statusFilter').value;
      return state.runs.filter(run => {{
        const text = `${{run.run_id}} ${{run.goal || ''}}`.toLowerCase();
        return (!query || text.includes(query)) && (!status || run.status === status);
      }});
    }}

    function renderRuns() {{
      const runs = filteredRuns();
      if (!runs.length) {{
        $('runList').innerHTML = '<div class="empty">No matching runs.</div>';
        return;
      }}
      $('runList').innerHTML = runs.map(run => `
        <button class="run-item ${{run.run_id === state.selectedRunId ? 'active' : ''}}" type="button" data-run-id="${{escapeHtml(run.run_id)}}">
          <span class="run-title">${{escapeHtml(run.run_id)}}</span>
          <span class="pill ${{escapeHtml(run.status || '')}}">${{escapeHtml(run.status || 'unknown')}}</span>
          <span class="run-goal">${{escapeHtml(run.goal || 'No goal recorded.')}}</span>
          <span class="subtle">${{formatNumber(run.event_count)}} events</span>
          <span class="subtle">${{escapeHtml(run.last_event || '')}}</span>
        </button>
      `).join('');
      document.querySelectorAll('[data-run-id]').forEach(button => {{
        button.addEventListener('click', () => loadRun(button.dataset.runId));
      }});
    }}

    function renderSummary() {{
      const run = state.runs.find(item => item.run_id === state.selectedRunId) || {{}};
      const items = [
        ['Status', run.status || state.metrics.status || 'unknown'],
        ['Turns', formatNumber(state.metrics.turns)],
        ['Bash Actions', formatNumber(state.metrics.bash_actions)],
        ['Policy Denials', formatNumber(state.metrics.policy_denials)],
        ['Approvals', formatNumber(state.metrics.approvals_requested)],
        ['Artifacts', formatNumber(state.metrics.artifact_bytes) + ' bytes'],
        ['Duration', formatDuration(state.metrics.total_duration_ms)],
        ['Last Event', run.last_event || ''],
        ['Started', shortTime(state.metrics.started_at)],
        ['Completed', shortTime(state.metrics.completed_at)]
      ];
      $('summary').innerHTML = items.map(([label, value]) => `
        <div class="metric">
          <div class="metric-label">${{escapeHtml(label)}}</div>
          <div class="metric-value">${{escapeHtml(value)}}</div>
        </div>
      `).join('');
    }}

    function populateEventTypes() {{
      const select = $('eventTypeFilter');
      const current = select.value;
      const types = Array.from(new Set(state.trace.map(event => event.event).filter(Boolean))).sort();
      select.innerHTML = '<option value="">All event types</option>' + types.map(type => (
        `<option value="${{escapeHtml(type)}}">${{escapeHtml(type)}}</option>`
      )).join('');
      if (types.includes(current)) select.value = current;
    }}

    function filteredTrace() {{
      const query = $('eventSearch').value.trim().toLowerCase();
      const type = $('eventTypeFilter').value;
      const focus = $('eventFocusFilter').value;
      return state.trace.filter(event => {{
        const eventType = event.event || '';
        const haystack = JSON.stringify(event).toLowerCase();
        return (!type || eventType === type)
          && (!focus || eventFocus[focus]?.has(eventType))
          && (!query || haystack.includes(query));
      }});
    }}

    function eventPreview(event) {{
      if (event.command) return event.command;
      if (event.reason) return event.reason;
      if (event.question) return event.question;
      if (event.final_answer) return event.final_answer;
      if (event.response_preview) return event.response_preview;
      if (event.observation) return `${{event.observation.kind || ''}} ${{event.observation.message || ''}}`;
      return '';
    }}

    function renderTimeline() {{
      const events = filteredTrace();
      if (!events.length) {{
        $('timelinePanel').innerHTML = '<div class="empty">No matching events.</div>';
        return;
      }}
      $('timelinePanel').innerHTML = events.map((event, index) => `
        <article class="event">
          <div>
            <div class="event-type">${{escapeHtml(event.event || 'event')}}</div>
            <div class="event-time">${{escapeHtml(shortTime(event.created_at))}}</div>
          </div>
          <div>
            <span class="pill ${{escapeHtml(event.status || '')}}">${{escapeHtml(event.status || 'no status')}}</span>
          </div>
          <div class="event-main">
            <div class="event-preview">${{escapeHtml(eventPreview(event))}}</div>
            <button type="button" data-event-index="${{index}}">Details</button>
            <pre class="event-json hidden" id="eventJson${{index}}">${{escapeHtml(JSON.stringify(event, null, 2))}}</pre>
          </div>
        </article>
      `).join('');
      document.querySelectorAll('[data-event-index]').forEach(button => {{
        button.addEventListener('click', () => {{
          const target = $('eventJson' + button.dataset.eventIndex);
          target.classList.toggle('hidden');
        }});
      }});
    }}

    function renderMetrics() {{
      $('metricsPanel').innerHTML = `<pre class="code-pane">${{escapeHtml(JSON.stringify(state.metrics, null, 2))}}</pre>`;
    }}

    function renderDiff() {{
      $('diffPanel').innerHTML = `<pre class="code-pane">${{escapeHtml(state.diff || 'No diff.')}}</pre>`;
    }}

    function renderActivePanel() {{
      $('timelinePanel').classList.toggle('hidden', state.activeTab !== 'timeline');
      $('metricsPanel').classList.toggle('hidden', state.activeTab !== 'metrics');
      $('diffPanel').classList.toggle('hidden', state.activeTab !== 'diff');
      $('timelineFilters').classList.toggle('hidden', state.activeTab !== 'timeline');
      document.querySelectorAll('[data-tab]').forEach(button => {{
        button.classList.toggle('active', button.dataset.tab === state.activeTab);
      }});
      renderTimeline();
      renderMetrics();
      renderDiff();
    }}

    function setAutoRefresh(enabled) {{
      state.autoRefresh = enabled;
      $('toggleRefresh').textContent = enabled ? 'Pause' : 'Resume';
      $('refreshState').textContent = enabled ? 'Live refresh on' : 'Live refresh paused';
      if (state.intervalId) clearInterval(state.intervalId);
      state.intervalId = enabled ? setInterval(() => refreshRuns().catch(console.error), 2000) : null;
    }}

    $('runSearch').addEventListener('input', renderRuns);
    $('statusFilter').addEventListener('change', renderRuns);
    $('eventSearch').addEventListener('input', renderTimeline);
    $('eventTypeFilter').addEventListener('change', renderTimeline);
    $('eventFocusFilter').addEventListener('change', renderTimeline);
    $('refreshNow').addEventListener('click', () => refreshRuns().catch(console.error));
    $('toggleRefresh').addEventListener('click', () => setAutoRefresh(!state.autoRefresh));
    document.querySelectorAll('[data-tab]').forEach(button => {{
      button.addEventListener('click', () => {{
        state.activeTab = button.dataset.tab;
        renderActivePanel();
      }});
    }});

    setAutoRefresh(true);
    refreshRuns().catch(error => {{
      $('runList').innerHTML = `<div class="empty">${{escapeHtml(error.message)}}</div>`;
    }});
  </script>
</body>
</html>"""
