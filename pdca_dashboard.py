#!/usr/bin/env python3
"""
PDCA Dashboard — 本地 Web 状态面板

Provides a lightweight HTTP server showing real-time PDClaw status.
Start with --dashboard or enable in config.ini.

Endpoints:
  /              — HTML dashboard page
  /api/status    — JSON snapshot
  /api/issue/<n> — Single issue detail
  /api/log       — SSE log stream (last N lines)
  /api/metrics   — Metrics summary
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable

log = logging.getLogger("pdca_dashboard")

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDClaw Dashboard</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;
        --green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff;--purple:#bc8cff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:var(--bg);color:var(--text);padding:24px}
  h1{font-size:20px;margin-bottom:4px}
  h2{font-size:15px;color:var(--muted);margin:16px 0 8px;text-transform:uppercase;letter-spacing:.5px}
  .status-bar{display:flex;gap:12px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
  .status-dot{width:10px;height:10px;border-radius:50%;background:var(--green);
              animation:pulse 2s infinite;display:inline-block;margin-right:6px}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .tag{font-size:11px;padding:2px 8px;border-radius:12px;background:var(--card);border:1px solid var(--border)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:20px}
  .stat-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}
  .stat-card .num{font-size:28px;font-weight:700}
  .stat-card .label{font-size:12px;color:var(--muted);margin-top:2px}
  .stat-card.green .num{color:var(--green)}
  .stat-card.blue .num{color:var(--blue)}
  .stat-card.yellow .num{color:var(--yellow)}
  .stat-card.purple .num{color:var(--purple)}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px}
  th{text-align:left;color:var(--muted);font-weight:500;padding:6px 10px;border-bottom:1px solid var(--border)}
  td{padding:8px 10px;border-bottom:1px solid var(--border)}
  tr:hover{background:var(--card)}
  .badge{font-size:11px;padding:1px 7px;border-radius:10px;font-weight:600}
  .badge-ok{background:rgba(63,185,80,.15);color:var(--green)}
  .badge-err{background:rgba(248,81,73,.15);color:var(--red)}
  .badge-wait{background:rgba(210,153,34,.15);color:var(--yellow)}
  .bar{height:6px;border-radius:3px;background:var(--border);overflow:hidden;margin-top:4px}
  .bar-fill{height:100%;border-radius:3px;background:var(--blue);transition:width .3s}
  .empty{color:var(--muted);font-style:italic;padding:12px 0}
  .footer{font-size:11px;color:var(--muted);margin-top:24px;text-align:center}
  .refresh{font-size:11px;color:var(--muted)}
</style>
</head>
<body>
  <h1>PDClaw Dashboard</h1>
  <div class="status-bar">
    <span><span class="status-dot"></span><strong id="status-text">Running</strong></span>
    <span class="tag" id="uptime">--</span>
    <span class="tag" id="cycles">--</span>
    <span class="refresh" id="refreshed">auto-refresh 5s</span>
  </div>

  <h2>Overview</h2>
  <div class="grid" id="overview"></div>

  <h2>Active Issues</h2>
  <div id="active-issues"></div>

  <h2>Recent AI Calls</h2>
  <div id="recent-calls"></div>

  <h2>Step Stats</h2>
  <div id="step-stats"></div>

  <div class="footer">PDClaw Dashboard &middot; <span id="clock"></span></div>

<script>
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

function fmtSec(s) {
  s = Math.ceil(s);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m + 'm ' + sec + 's';
}
function fmtTs(ts) {
  if (!ts) return '--';
  return new Date(ts).toLocaleTimeString();
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    render(d);
  } catch(e) { console.error(e); }
  $('#clock').textContent = new Date().toLocaleTimeString();
  $('#refreshed').textContent = 'refreshed ' + new Date().toLocaleTimeString();
}

function render(d) {
  $('#uptime').textContent = 'uptime ' + fmtSec(d.uptime_sec);
  $('#cycles').textContent = 'cycles ' + d.poll_cycles;

  // Overview cards
  const active = Object.keys(d.active_issues||{}).length;
  const completed = Object.keys(d.completed_issues||{}).length;
  const totalCalls = (d.recent_calls||[]).length;
  const successCalls = (d.recent_calls||[]).filter(c=>c.ok).length;
  $('#overview').innerHTML =
    `<div class="stat-card green"><div class="num">${active}</div><div class="label">Active Issues</div></div>` +
    `<div class="stat-card blue"><div class="num">${completed}</div><div class="label">Completed</div></div>` +
    `<div class="stat-card purple"><div class="num">${successCalls}/${totalCalls}</div><div class="label">Recent AI Calls (ok/total)</div></div>` +
    `<div class="stat-card yellow"><div class="num">${d.poll_cycles}</div><div class="label">Poll Cycles</div></div>`;

  // Active issues
  const ai = d.active_issues || {};
  if (Object.keys(ai).length === 0) {
    $('#active-issues').innerHTML = '<div class="empty">No active issues</div>';
  } else {
    let html = '<table><tr><th>Issue</th><th>Title</th><th>Calls</th><th>AI Time</th><th>Last Transition</th></tr>';
    for (const [num, iss] of Object.entries(ai)) {
      html += `<tr>
        <td><a href="/issue/${num}" style="color:var(--blue)">#${num}</a></td>
        <td>${esc(iss.title||'')}</td>
        <td>${iss.calls}</td>
        <td>${fmtSec(iss.total_sec||0)}</td>
        <td>${iss.last_transition ? esc(iss.last_transition.from+' → '+iss.last_transition.to) : '—'}</td>
      </tr>`;
    }
    html += '</table>';
    $('#active-issues').innerHTML = html;
  }

  // Recent AI calls
  const calls = d.recent_calls || [];
  if (calls.length === 0) {
    $('#recent-calls').innerHTML = '<div class="empty">No AI calls yet</div>';
  } else {
    let html = '<table><tr><th>Time</th><th>Issue</th><th>Step</th><th>Result</th><th>Elapsed</th><th>Tokens</th></tr>';
    for (const c of calls.slice().reverse()) {
      html += `<tr>
        <td>${fmtTs(c.ts)}</td>
        <td>#${c.issue}</td>
        <td>${esc(c.step)}</td>
        <td><span class="badge ${c.ok?'badge-ok':'badge-err'}">${c.ok?'OK':'FAIL'}</span></td>
        <td>${c.elapsed}s</td>
        <td>~${c.tokens}</td>
      </tr>`;
    }
    html += '</table>';
    $('#recent-calls').innerHTML = html;
  }

  // Step stats
  const ss = d.step_stats || {};
  if (Object.keys(ss).length === 0) {
    $('#step-stats').innerHTML = '<div class="empty">No step data yet</div>';
  } else {
    let html = '<table><tr><th>Step</th><th>Success</th><th>Failure</th><th>Total Time</th><th>Rate</th></tr>';
    for (const [step, s] of Object.entries(ss)) {
      const total = (s.success||0) + (s.failure||0);
      const rate = total > 0 ? Math.round(s.success/total*100) : 0;
      html += `<tr>
        <td><strong>${esc(step)}</strong></td>
        <td><span class="badge badge-ok">${s.success||0}</span></td>
        <td><span class="badge badge-err">${s.failure||0}</span></td>
        <td>${fmtSec(s.total_sec||0)}</td>
        <td><div class="bar"><div class="bar-fill" style="width:${rate}%"></div></div>${rate}%</td>
      </tr>`;
    }
    html += '</table>';
    $('#step-stats').innerHTML = html;
  }
}
function esc(s) { const d=document.createElement('div');d.textContent=s;return d.innerHTML; }
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


_ISSUE_DETAIL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Issue Detail — PDClaw</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;
        --green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff;--purple:#bc8cff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:var(--bg);color:var(--text);padding:24px;max-width:900px;margin:0 auto}
  a{color:var(--blue);text-decoration:none}
  a:hover{text-decoration:underline}
  h1{font-size:20px;margin-bottom:4px}
  h2{font-size:15px;color:var(--muted);margin:20px 0 10px;text-transform:uppercase;letter-spacing:.5px}
  .back{margin-bottom:16px;display:inline-block}
  .meta{display:flex;gap:20px;flex-wrap:wrap;margin:12px 0}
  .meta-item{font-size:13px}
  .meta-item strong{color:var(--muted);font-weight:500}
  .steps{display:flex;gap:8px;margin:16px 0;flex-wrap:wrap}
  .step-pill{font-size:13px;padding:6px 16px;border-radius:20px;background:var(--card);
             border:2px solid var(--border);text-transform:uppercase;font-weight:600;letter-spacing:.5px}
  .step-pill.done{border-color:var(--green);background:rgba(63,185,80,.1);color:var(--green)}
  .step-pill.active{border-color:var(--blue);background:rgba(88,166,255,.1);color:var(--blue);animation:pulse-border 2s infinite}
  .step-pill.pending{border-color:var(--border);color:var(--muted)}
  @keyframes pulse-border{0%,100%{border-color:var(--blue)}50%{border-color:transparent}}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px}
  th{text-align:left;color:var(--muted);font-weight:500;padding:6px 10px;border-bottom:1px solid var(--border)}
  td{padding:8px 10px;border-bottom:1px solid var(--border)}
  tr:hover{background:var(--card)}
  .badge{font-size:11px;padding:1px 7px;border-radius:10px;font-weight:600}
  .badge-ok{background:rgba(63,185,80,.15);color:var(--green)}
  .badge-err{background:rgba(248,81,73,.15);color:var(--red)}
  .badge-info{background:rgba(88,166,255,.15);color:var(--blue)}
  .empty{color:var(--muted);font-style:italic;padding:12px 0}
  .timeline{position:relative;padding-left:24px;margin:12px 0}
  .timeline::before{content:'';position:absolute;left:8px;top:0;bottom:0;width:2px;background:var(--border)}
  .tl-item{position:relative;margin-bottom:14px;padding-left:8px}
  .tl-item::before{content:'';position:absolute;left:-20px;top:6px;width:10px;height:10px;border-radius:50%;
    background:var(--card);border:2px solid var(--border)}
  .tl-item.plan::before{border-color:var(--blue)}
  .tl-item.do::before{border-color:var(--purple)}
  .tl-item.check::before{border-color:var(--yellow)}
  .tl-item.act::before{border-color:var(--green)}
  .tl-step{font-weight:600;text-transform:uppercase;font-size:12px;letter-spacing:.5px}
  .tl-time{font-size:11px;color:var(--muted);margin-left:8px}
  .tl-detail{font-size:13px;color:var(--muted);margin-top:2px}
  .progress-bar{height:8px;border-radius:4px;background:var(--border);overflow:hidden;margin:16px 0}
  .progress-fill{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--blue),var(--green));transition:width .5s}
  .stat-row{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}
  .stat-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 16px;min-width:140px}
  .stat-card .num{font-size:24px;font-weight:700}
  .stat-card .label{font-size:11px;color:var(--muted)}
  .stat-card.green .num{color:var(--green)}
  .stat-card.blue .num{color:var(--blue)}
  .stat-card.purple .num{color:var(--purple)}
  .footer{font-size:11px;color:var(--muted);margin-top:24px;text-align:center}
</style>
</head>
<body>
  <a href="/" class="back">&larr; Back to Dashboard</a>
  <h1 id="issue-title">Issue #—</h1>
  <div class="meta" id="issue-meta"></div>

  <div class="progress-bar"><div class="progress-fill" id="progress-bar" style="width:0%"></div></div>

  <h2>PDCA Steps</h2>
  <div class="steps" id="pdca-steps"></div>

  <div class="stat-row" id="stats-row"></div>

  <h2>State Transitions</h2>
  <div id="transitions"></div>

  <h2>AI Calls</h2>
  <div id="ai-calls"></div>

  <div class="footer">PDClaw &middot; <span id="clock"></span></div>

<script>
const issueNum = parseInt(location.pathname.split('/').pop());
const PDCA = ['plan', 'do', 'check', 'act'];
const STEP_COLORS = {plan:'var(--blue)', do:'var(--purple)', check:'var(--yellow)', act:'var(--green)'};

function esc(s) { const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
function fmtSec(s) {
  if (!s) return '0s';
  s = Math.ceil(s);
  if (s<60) return s+'s';
  return Math.floor(s/60)+'m '+(s%60)+'s';
}
function fmtTs(ts) {
  if (!ts) return '--';
  return new Date(ts).toLocaleTimeString();
}

async function refresh() {
  try {
    const r = await fetch('/api/issue/'+issueNum+'/detail');
    if (!r.ok) { document.getElementById('issue-title').textContent='Issue not found'; return; }
    const d = await r.json();
    render(d);
  } catch(e) { console.error(e); }
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}

function render(d) {
  document.getElementById('issue-title').innerHTML =
    '<a href="https://github.com/issues/'+d.number+'" target="_blank">#'+d.number+'</a> '+esc(d.title);
  document.title = '#'+d.number+' '+d.title+' — PDClaw';

  const meta = document.getElementById('issue-meta');
  const created = new Date(d.created).toLocaleString();
  const completed = d.completed ? new Date(d.completed).toLocaleString() : null;
  meta.innerHTML =
    '<span class="meta-item"><strong>Created:</strong> '+created+'</span>' +
    (completed ? '<span class="meta-item"><strong>Completed:</strong> '+completed+'</span>' : '') +
    '<span class="meta-item"><strong>Status:</strong> '+(d.completed?'Completed':'In Progress')+'</span>';

  // Progress bar
  const transitions = d.transitions||[];
  const stepsHit = new Set(transitions.map(t=>t.to));
  const doneCount = PDCA.filter(s=>stepsHit.has(s)).length;
  document.getElementById('progress-bar').style.width = (doneCount/4*100)+'%';

  // PDCA Steps pills
  const stepsEl = document.getElementById('pdca-steps');
  let stepsHtml = '';
  for (const step of PDCA) {
    let cls = 'pending';
    if (stepsHit.has(step)) cls = 'done';
    // Last transition tells us current step if issue is active
    if (!d.completed && transitions.length>0 && transitions[transitions.length-1].to===step) cls='active';
    stepsHtml += '<div class="step-pill '+cls+'">'+step+'</div>';
  }
  stepsEl.innerHTML = stepsHtml;

  // Stats
  document.getElementById('stats-row').innerHTML =
    '<div class="stat-card blue"><div class="num">'+d.calls+'</div><div class="label">AI Calls</div></div>' +
    '<div class="stat-card purple"><div class="num">'+fmtSec(d.total_sec)+'</div><div class="label">AI Time</div></div>' +
    '<div class="stat-card green"><div class="num">'+transitions.length+'</div><div class="label">Transitions</div></div>';

  // Transitions timeline
  const tlEl = document.getElementById('transitions');
  if (transitions.length===0) {
    tlEl.innerHTML = '<div class="empty">No transitions recorded</div>';
  } else {
    let html = '<div class="timeline">';
    for (const t of transitions) {
      html += '<div class="tl-item '+esc(t.to||'')+'">' +
        '<span class="tl-step" style="color:'+(STEP_COLORS[t.to]||'var(--text)')+'">'+esc(t.to)+'</span>' +
        '<span class="tl-time">'+fmtTs(t.ts)+'</span>' +
        '<div class="tl-detail">'+esc(t.from||'—')+' &rarr; '+esc(t.to)+'</div>' +
      '</div>';
    }
    html += '</div>';
    tlEl.innerHTML = html;
  }

  // AI Calls table
  const calls = d.ai_calls||[];
  const callsEl = document.getElementById('ai-calls');
  if (calls.length===0) {
    callsEl.innerHTML = '<div class="empty">No AI calls yet</div>';
  } else {
    let html = '<table><tr><th>Time</th><th>Step</th><th>Result</th><th>Elapsed</th><th>Tokens</th></tr>';
    for (const c of calls.slice().reverse()) {
      html += '<tr>' +
        '<td>'+fmtTs(c.ts)+'</td>' +
        '<td><span class="badge badge-info">'+esc(c.step)+'</span></td>' +
        '<td><span class="badge '+(c.ok?'badge-ok':'badge-err')+'">'+(c.ok?'OK':'FAIL')+'</span></td>' +
        '<td>'+c.elapsed+'s</td>' +
        '<td>~'+c.tokens+'</td>' +
      '</tr>';
    }
    html += '</table>';
    callsEl.innerHTML = html;
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler serving dashboard and API endpoints."""

    # Class-level references set by the server factory
    get_snapshot: Callable[[], dict] = staticmethod(lambda: {})
    get_issue_detail: Callable[[int], dict | None] = staticmethod(lambda n: None)
    log_lines: list[str] = []
    max_log_lines: int = 200

    def log_message(self, format, *args):
        pass  # Suppress default access logs

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content, status=200):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/":
            self._html(_DASHBOARD_HTML)

        elif path == "/api/status":
            self._json(self.get_snapshot())

        elif path.startswith("/api/issue/") and path.endswith("/detail"):
            # /api/issue/<n>/detail — full issue detail JSON
            try:
                parts = path.strip("/").split("/")
                num = int(parts[2])
                detail = self.get_issue_detail(num)
                if detail is None:
                    self._json({"error": "issue not found"}, 404)
                else:
                    self._json(detail)
            except (ValueError, IndexError):
                self._json({"error": "bad issue number"}, 400)

        elif path.startswith("/issue/"):
            # /issue/<n> — HTML detail page
            try:
                num = int(path.split("/")[-1])
                self._html(_ISSUE_DETAIL_HTML)
            except ValueError:
                self.send_response(404)
                self.end_headers()

        elif path.startswith("/api/issue/"):
            try:
                num = int(path.split("/")[-1])
                snap = self.get_snapshot()
                active = snap.get("active_issues", {}).get(str(num))
                completed = snap.get("completed_issues", {}).get(str(num))
                self._json({
                    "issue": num,
                    "active": active,
                    "completed": completed,
                })
            except ValueError:
                self._json({"error": "bad issue number"}, 400)

        elif path == "/api/log":
            body = "\n".join(self.log_lines).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/metrics":
            self._json(self.get_snapshot())

        else:
            self.send_response(404)
            self.end_headers()


class DashboardServer:
    """Background HTTP server for the PDCA dashboard."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9191,
        get_snapshot: Callable[[], dict] | None = None,
        get_issue_detail: Callable[[int], dict | None] | None = None,
    ):
        self.host = host
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # Wire up the snapshot callback
        DashboardHandler.get_snapshot = staticmethod(get_snapshot) if get_snapshot else staticmethod(lambda: {})
        DashboardHandler.get_issue_detail = staticmethod(get_issue_detail) if get_issue_detail else staticmethod(lambda n: None)

    def start(self) -> None:
        if self._running:
            return
        self._server = HTTPServer((self.host, self.port), DashboardHandler)
        self._running = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Dashboard listening on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        log.info("Dashboard stopped")


def start_dashboard(
    host: str = "0.0.0.0",
    port: int = 9191,
    get_snapshot: Callable[[], dict] | None = None,
    get_issue_detail: Callable[[int], dict | None] | None = None,
) -> DashboardServer:
    server = DashboardServer(
        host=host, port=port,
        get_snapshot=get_snapshot,
        get_issue_detail=get_issue_detail,
    )
    server.start()
    return server
