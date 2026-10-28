"""Zero-dependency web UI for the annotation planner, styled with the
Mistral AI design system (sunset palette, cream surfaces, editorial serif
display type, sunset stripe footer).

Serves a dashboard (snapshot + breakdown table + daily grid) and edit forms
for team rows, lanes, progress, and the Google Sheet sync.

Run:  python3 -m planner.cli --dir data web --port 8000
"""

from __future__ import annotations

import html
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .planning import build_plan, plan_dates
from .store import (
    DATA_TYPES,
    STATUSES,
    Lane,
    ProgressEntry,
    Store,
    StoreError,
    TeamRow,
    parse_date,
)

# Mistral AI design tokens (from the public mistral.ai design system)
CSS = """
:root{
  --primary:#fa520f; --primary-deep:#cc3a05;
  --sunshine-300:#ffd06a; --sunshine-500:#ffb83e; --sunshine-700:#ffa110;
  --sunshine-800:#ff8105; --sunshine-900:#ff8a00; --yellow:#ffd900;
  --cream:#fff8e0; --cream-soft:#fffaeb; --cream-deeper:#fff0c2;
  --beige-deep:#e6d5a8;
  --ink:#1f1f1f; --charcoal:#2c2c2c; --slate:#4a4a4a; --steel:#6a6a6a;
  --stone:#8a8a8a; --muted:#a8a8a8;
  --hairline:#e5e5e5; --hairline-soft:#ededed; --hairline-strong:#c7c7c7;
  --canvas:#ffffff; --surface:#fafafa;
  --ok:#2e7d32; --warn-bg:#fff3e0; --warn:#b26a00; --err-bg:#fdecea; --err:#c62828;
}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:16px;line-height:1.55;}
.serif{font-family:"PP Editorial Old","Times New Roman",Georgia,serif;}
header{background:var(--ink);color:#fff;padding:14px 32px;display:flex;
  align-items:center;gap:24px;flex-wrap:wrap;}
header .brand{font-size:20px;font-weight:600;letter-spacing:-.3px;display:flex;align-items:center;gap:10px;}
header .brand .mark{width:26px;height:26px;border-radius:6px;
  background:linear-gradient(135deg,var(--yellow) 0%,var(--sunshine-700) 45%,var(--primary) 100%);}
header nav{display:flex;gap:4px;flex-wrap:wrap;}
header nav a{color:#fff;text-decoration:none;font-size:14px;font-weight:500;
  padding:8px 12px;border-radius:8px;transition:background .15s ease;}
header nav a:hover{background:rgba(255,255,255,.12);}
header nav a.active{background:var(--primary);}
main{max-width:1280px;margin:0 auto;padding:32px;}
h1{font-size:34px;font-weight:500;letter-spacing:-.5px;margin:0 0 4px;}
h2{font-size:22px;font-weight:500;margin:40px 0 12px;}
h3{font-size:18px;font-weight:500;margin:24px 0 8px;}
.sub{color:var(--steel);font-size:14px;margin:0 0 24px;}
table{border-collapse:collapse;width:100%;font-size:14px;background:var(--canvas);}
th,td{border-bottom:1px solid var(--hairline);padding:10px 12px;text-align:left;vertical-align:middle;}
th{color:var(--steel);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.6px;}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}
tbody tr:hover{background:var(--surface);}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin:24px 0;}
.card{background:var(--cream-soft);border:1px solid var(--beige-deep);border-radius:12px;padding:20px 24px;}
.card .label{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--slate);}
.card .big{font-size:40px;font-weight:400;line-height:1.1;margin-top:6px;letter-spacing:-1px;}
.card .hint{font-size:12px;color:var(--steel);margin-top:4px;}
.panel{background:var(--canvas);border:1px solid var(--hairline);border-radius:12px;padding:24px;margin:16px 0;}
.panel.cream{background:var(--cream);border-color:var(--beige-deep);}
button,.btn{background:var(--primary);color:#fff;border:none;border-radius:8px;
  padding:10px 20px;font-size:14px;font-weight:500;cursor:pointer;
  font-family:inherit;transition:background .15s ease;text-decoration:none;display:inline-block;}
button:hover,.btn:hover{background:var(--primary-deep);}
button.secondary,.btn.secondary{background:var(--canvas);color:var(--ink);border:1px solid var(--hairline-strong);}
button.secondary:hover{background:var(--surface);}
button.danger{background:var(--err);}
input,select{font-family:inherit;font-size:14px;padding:8px 10px;border:1px solid var(--hairline-strong);
  border-radius:8px;background:var(--canvas);color:var(--ink);min-width:90px;}
input:focus,select:focus{outline:2px solid var(--sunshine-500);outline-offset:0;border-color:var(--sunshine-700);}
input[type=number]{width:110px;}
input[type=date]{min-width:150px;}
.flash{background:var(--cream);border:1px solid var(--beige-deep);color:var(--ink);
  padding:12px 16px;border-radius:8px;margin:16px 0;font-size:14px;}
.err{background:var(--err-bg);border:1px solid #f5c6c0;color:var(--err);
  padding:12px 16px;border-radius:8px;margin:16px 0;font-size:14px;}
.warnbox{background:var(--warn-bg);border:1px solid var(--sunshine-300);border-radius:12px;
  padding:16px 20px;margin:16px 0;}
.warnbox h2{color:var(--warn);margin-top:0;}
.warnbox ul{margin:8px 0 0;padding-left:20px;color:var(--charcoal);font-size:14px;}
.badge{display:inline-block;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:600;}
.badge.not_started{background:var(--hairline-soft);color:var(--slate);}
.badge.in_progress{background:var(--sunshine-300);color:var(--ink);}
.badge.completed{background:#dcedc8;color:var(--ok);}
.badge.stopped{background:var(--err-bg);color:var(--err);}
.gridwrap{overflow-x:auto;border:1px solid var(--hairline);border-radius:12px;}
.grid td{min-width:52px;text-align:right;font-variant-numeric:tabular-nums;border-bottom:1px solid var(--hairline-soft);}
.grid td.rowlabel{min-width:200px;text-align:left;font-weight:500;}
.grid th{position:sticky;top:0;background:var(--cream);}
form.inline{display:inline;}
.rowform td{padding:6px 8px;}
.muted{color:var(--muted);font-size:13px;}
code{font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;font-size:13px;
  background:var(--surface);padding:2px 6px;border-radius:4px;}
.stripe{height:10px;margin-top:48px;
  background:linear-gradient(90deg,var(--primary) 0%,var(--sunshine-800) 30%,var(--yellow) 60%,var(--cream) 100%);}
footer{max-width:1280px;margin:0 auto;padding:24px 32px 40px;color:var(--stone);font-size:13px;}
.stat-row{display:flex;gap:48px;flex-wrap:wrap;margin:16px 0;}
.stat .big{font-size:56px;font-weight:400;line-height:1.1;letter-spacing:-1px;}
.stat .label{font-size:13px;color:var(--steel);text-transform:uppercase;letter-spacing:.8px;font-weight:600;}
@media(max-width:768px){main{padding:20px;}h1{font-size:26px;}.card .big{font-size:30px;}}
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Annotation Planner</title>
<style>{css}</style>
</head>
<body>
<header>
  <div class="brand"><span class="mark"></span>Annotation Planner</div>
  <nav>{nav}</nav>
</header>
<main>
{flash}
{body}
</main>
<div class="stripe"></div>
<footer>Annotation Planner · data lives in CSV / Google Sheets · styled with the Mistral AI design system</footer>
</body>
</html>
"""

DASH = "\u2014"  # em dash placeholder

NAV_ITEMS = [
    ("/", "Dashboard"),
    ("/team", "Team"),
    ("/lanes", "Tasks"),
    ("/progress", "Progress"),
    ("/sync", "Sync"),
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _options(values: list[str], selected: str) -> str:
    return "".join(
        f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(v)}</option>'
        for v in values
    )


def _badge(status: str) -> str:
    return f'<span class="badge {esc(status)}">{esc(status)}</span>'


class PlannerHandler(BaseHTTPRequestHandler):
    server_version = "AnnotationPlanner/2.0"

    def log_message(self, format: str, *args: object) -> None:
        pass

    @property
    def st(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _page(self, body: str, title: str, flash: str = "", error: str = "") -> None:
        current = urlparse(self.path).path
        nav = "".join(
            '<a href="{}"{}>{}</a>'.format(
                esc(href),
                ' class="active"' if href == current else "",
                esc(label),
            )
            for href, label in NAV_ITEMS
        )
        flash_html = ""
        if flash:
            flash_html = f'<div class="flash">{esc(flash)}</div>'
        if error:
            flash_html += f'<div class="err">{esc(error)}</div>'
        self._send(_PAGE.format(title=title, css=CSS, nav=nav, flash=flash_html, body=body))

    def _redirect(self, location: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    # ----- routing ---------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._page(self._dashboard(), "Dashboard")
            elif path == "/team":
                self._page(self._team_page(), "Team")
            elif path == "/lanes":
                self._page(self._lanes_page(), "Tasks")
            elif path == "/progress":
                self._page(self._progress_page(), "Progress")
            elif path == "/sync":
                self._page(self._sync_page(), "Sync")
            else:
                self._send("not found", 404)
        except StoreError as exc:
            self._page(f"<h1>Error</h1><p>{esc(exc)}</p>", "Error", error=str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))

        def get(k: str, d: str = "") -> str:
            return form.get(k, [d])[0].strip()

        try:
            if path == "/team/add":
                self.st.add_team_row(
                    TeamRow(
                        from_date=parse_date(get("from_date"), "from_date"),
                        to_date=parse_date(get("to_date"), "to_date"),
                        total_hc=float(get("total_hc", "0")),
                        throughput_per_day=float(get("throughput_per_day", "0")),
                    )
                )
                self._redirect("/team")
            elif path == "/team/clear":
                self.st.clear_team_rows()
                self._redirect("/team")
            elif path == "/lanes/add":
                self.st.set_lane(
                    Lane(
                        data_type=get("data_type"),
                        purpose=get("purpose"),
                        batch=get("batch"),
                        total_required=int(get("total_required", "0")),
                        assigned_hc=float(get("assigned_hc", "0")),
                        status=get("status", "not_started"),
                    )
                )
                self._redirect("/lanes")
            elif path == "/lanes/update":
                self.st.set_lane(
                    Lane(
                        data_type=get("data_type"),
                        purpose=get("purpose"),
                        batch=get("batch"),
                        total_required=int(get("total_required", "0")),
                        assigned_hc=float(get("assigned_hc", "0")),
                        status=get("status", "not_started"),
                    )
                )
                self._redirect("/lanes")
            elif path == "/lanes/delete":
                self.st.delete_lane(get("data_type"), get("purpose"), get("batch"))
                self._redirect("/lanes")
            elif path == "/progress/add":
                day = parse_date(get("date"))
                if day is None:
                    raise StoreError("date is required (YYYY-MM-DD)")
                dt, pu, ba = _split_lane_field(get("lane"))
                self.st.log_progress(
                    ProgressEntry(
                        date=day,
                        data_type=dt,
                        purpose=pu,
                        batch=ba,
                        items_completed=int(get("items", "0")),
                    )
                )
                self._redirect("/progress")
            elif path == "/sync/set":
                from .sheets import set_sheet_id

                set_sheet_id(self.st, get("sheet_id"))
                self._redirect("/sync")
            elif path == "/sync/pull":
                from .sheets import pull

                result = pull(self.st, get("sheet_id") or None)
                self._redirect(
                    f"/sync?ok=1&rows={result.team_rows}&lanes={result.lanes_replaced}"
                    f"&added={result.progress_added}&merged={result.progress_updated}"
                )
            else:
                self._send("not found", 404)
        except (StoreError, ValueError) as exc:
            self._page(f"<h1>Could not save</h1><p>{esc(exc)}</p>", "Error", error=str(exc))

    # ----- pages -----------------------------------------------------

    def _dashboard(self) -> str:
        today = date.today()
        report = build_plan(self.st, today)
        snap = report.snapshot

        cards = f"""
<div class="cards">
  <div class="card"><div class="label">Total required</div><div class="big serif">{snap.total_required}</div></div>
  <div class="card"><div class="label">Completed</div><div class="big serif">{snap.total_completed}</div></div>
  <div class="card"><div class="label">In progress</div><div class="big serif">{snap.total_in_progress}</div><div class="hint">items remaining on in-progress tasks</div></div>
  <div class="card"><div class="label">Overall</div><div class="big serif">{snap.pct_complete:.1f}%</div></div>
  <div class="card"><div class="label">Peak team HC</div><div class="big serif">{report.team.peak_hc:g}</div></div>
  <div class="card"><div class="label">Throughput</div><div class="big serif">{report.team.peak_throughput:g}</div><div class="hint">items / HC / day</div></div>
</div>
"""

        rows = []
        for lp in report.lanes:
            lane = lp.lane
            team_hc = ""
            throughput = ""
            if lp.start_date is not None:
                trow = self.st.team_on(lp.start_date)
                if trow is not None:
                    team_hc = f"{trow.total_hc:g}"
                    throughput = f"{trow.throughput_per_day:g}"
            rows.append(
                "<tr>"
                f"<td><strong>{esc(lane.data_type)}</strong></td>"
                f"<td>{esc(lane.purpose)}</td>"
                f"<td>{esc(lane.batch)}</td>"
                f'<td class="num">{lane.total_required}</td>'
                f'<td class="num">{lp.completed}</td>'
                f'<td class="num">{lp.pct_complete:.1f}%</td>'
                f'<td class="num">{team_hc or DASH}</td>'
                f'<td class="num">{lane.assigned_hc:g}</td>'
                f'<td class="num">{throughput or DASH}</td>'
                f'<td class="num">{lp.capacity_per_day:.1f}</td>'
                f"<td>{_badge(lane.status)}</td>"
                f'<td class="num">{lp.start_date.isoformat() if lp.start_date else "—"}</td>'
                f'<td class="num">{lp.finish_date.isoformat() if lp.finish_date else "—"}</td>'
                "</tr>"
            )
        table = f"""
<div class="panel">
<table>
<thead><tr><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Required</th>
<th class="num">Completed</th><th class="num">%</th>
<th class="num">Total HC</th><th class="num">Assigned HC</th><th class="num">Throughput</th><th class="num">Capacity/day</th>
<th>Status</th><th class="num">Start</th><th class="num">Finish</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan="13" class="muted">No tasks yet — add them on the Tasks page.</td></tr>'}</tbody>
</table>
</div>
"""

        dates = plan_dates(report)
        grid = ""
        if dates:
            from .planning import LaneKey

            header = "".join(f"<th>{d.isoformat()[5:]}</th>" for d in dates)
            body_rows = []
            for lp in report.lanes:
                lane = lp.lane
                key_label = f"{lane.data_type} / {lane.purpose} / {lane.batch}"
                cells = []
                for d in dates:
                    n = report.daily.get(d, {}).get(
                        LaneKey(lane.data_type, lane.purpose, lane.batch), 0
                    )
                    cells.append(f'<td>{"" if n == 0 else n}</td>')
                body_rows.append(
                    f'<tr><td class="rowlabel">{esc(key_label)}</td>{"".join(cells)}</tr>'
                )
            grid = f"""
<h2>Daily completions</h2>
<p class="sub">One column per date; cells show items completed that day.</p>
<div class="gridwrap"><table class="grid">
<thead><tr><th>Lane</th>{header}</tr></thead>
<tbody>{''.join(body_rows)}</tbody>
</table></div>
"""

        warnings = ""
        if report.warnings:
            items = "".join(f"<li>{esc(w)}</li>" for w in report.warnings)
            warnings = f'<div class="warnbox"><h2>Attention needed</h2><ul>{items}</ul></div>'

        return (
            f'<h1 class="serif">Dashboard</h1>'
            f'<p class="sub">Snapshot as of {today.isoformat()} · batches run in order within each type/purpose group</p>'
            f"{cards}<h2>Task breakdown</h2>{table}{grid}{warnings}"
        )

    def _team_page(self) -> str:
        rows = []
        for r in self.st.list_team_rows():
            rows.append(
                "<tr>"
                f"<td>{_fmt(r.from_date)}</td>"
                f"<td>{_fmt(r.to_date)}</td>"
                f'<td class="num">{r.total_hc:g}</td>'
                f'<td class="num">{r.throughput_per_day:g}</td>'
                "</tr>"
            )
        table = f"""
<div class="panel">
<table>
<thead><tr><th>From</th><th>To</th><th class="num">Total HC</th><th class="num">Throughput (items/HC/day)</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan="4" class="muted">No rows yet.</td></tr>'}</tbody>
</table>
</div>
"""
        return f"""
<h1 class="serif">Team &amp; throughput</h1>
<p class="sub">Capacity for a task = assigned HC × throughput. Use date ranges when HC or throughput changes over time — rows must not overlap.</p>
{table}
<div class="panel cream">
<h3>Add a team row</h3>
<form method="post" action="/team/add">
<table>
<tr><th>From (optional)</th><td><input type="date" name="from_date"></td>
    <th>To (optional)</th><td><input type="date" name="to_date"></td></tr>
<tr><th>Total HC</th><td><input type="number" step="0.5" min="0" name="total_hc" required></td>
    <th>Throughput</th><td><input type="number" step="0.1" min="0.1" name="throughput_per_day" required></td></tr>
</table>
<p><button type="submit">Add row</button>
&nbsp;<button type="submit" class="secondary" formaction="/team/clear">Clear all rows</button></p>
</form>
</div>
"""

    def _lanes_page(self) -> str:
        rows = []
        for lane in self.st.list_lanes():
            rows.append(
                '<tr class="rowform"><form method="post" action="/lanes/update">'
                f'<input type="hidden" name="data_type" value="{esc(lane.data_type)}">'
                f'<input type="hidden" name="purpose" value="{esc(lane.purpose)}">'
                f'<input type="hidden" name="batch" value="{esc(lane.batch)}">'
                f"<td><strong>{esc(lane.data_type)}</strong></td>"
                f"<td>{esc(lane.purpose)}</td>"
                f"<td>{esc(lane.batch)}</td>"
                f'<td class="num"><input type="number" min="0" name="total_required" value="{lane.total_required}"></td>'
                f'<td class="num"><input type="number" step="0.5" min="0" name="assigned_hc" value="{lane.assigned_hc:g}"></td>'
                f'<td><select name="status">{_options(list(STATUSES), lane.status)}</select></td>'
                f'<td><button type="submit">Save</button> '
                f'<button type="submit" class="danger" formaction="/lanes/delete">Delete</button></td>'
                "</form></tr>"
            )
        table = f"""
<div class="panel">
<table>
<thead><tr><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Required</th>
<th class="num">Assigned HC</th><th>Status</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</div>
"""
        return f"""
<h1 class="serif">Tasks</h1>
<p class="sub">Each row is one labeling task: a batch broken down by data type and purpose. Edit any cell and press Save.</p>
{table}
<div class="panel cream">
<h3>Add a task</h3>
<form method="post" action="/lanes/add">
<table>
<tr><th>Type</th><td><select name="data_type">{_options(list(DATA_TYPES), "Eval")}</select></td>
    <th>Purpose</th><td><input name="purpose" placeholder="e.g. qc" required></td>
    <th>Batch</th><td><input name="batch" placeholder="B1" required style="width:70px"></td></tr>
<tr><th>Required</th><td><input type="number" min="0" name="total_required" value="0"></td>
    <th>Assigned HC</th><td><input type="number" step="0.5" min="0" name="assigned_hc" value="0"></td>
    <th>Status</th><td><select name="status">{_options(list(STATUSES), "not_started")}</select></td></tr>
</table>
<p><button type="submit">Add task</button></p>
</form>
</div>
"""

    def _progress_page(self) -> str:
        lanes = self.st.list_lanes()
        lane_opts = "".join(
            f'<option value="{esc(l.data_type)}|{esc(l.purpose)}|{esc(l.batch)}">'
            f"{esc(l.data_type)} / {esc(l.purpose)} / {esc(l.batch)}</option>"
            for l in lanes
        )
        entries = []
        for p in sorted(self.st.list_progress(), key=lambda e: e.date, reverse=True):
            entries.append(
                f"<tr><td>{p.date.isoformat()}</td><td>{esc(p.data_type)}</td>"
                f"<td>{esc(p.purpose)}</td><td>{esc(p.batch)}</td>"
                f'<td class="num">{p.items_completed}</td></tr>'
            )
        log = f"""
<div class="panel">
<table>
<thead><tr><th>Date</th><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Items</th></tr></thead>
<tbody>{''.join(entries) if entries else '<tr><td colspan="5" class="muted">Nothing logged yet.</td></tr>'}</tbody>
</table>
</div>
"""
        return f"""
<h1 class="serif">Progress</h1>
<p class="sub">Log items completed each day. These feed the dashboard snapshot and the daily grid.</p>
<div class="panel cream">
<form method="post" action="/progress/add">
<table>
<tr><th>Date</th><td><input type="date" name="date" required></td>
    <th>Task</th><td><select name="lane">{lane_opts}</select></td>
    <th>Items completed</th><td><input type="number" min="1" name="items" value="1" required></td></tr>
</table>
<p><button type="submit">Log</button></p>
</form>
</div>
<h2>History</h2>
{log}
"""

    def _sync_page(self) -> str:
        from .sheets import get_sheet_id

        sheet_id = get_sheet_id(self.st) or ""
        query = parse_qs(urlparse(self.path).query)
        banner = ""
        if query.get("ok", [""])[0] == "1":
            banner = (
                '<div class="flash">Sync complete: '
                f"{query.get('rows', ['0'])[0]} team row(s), "
                f"{query.get('lanes', ['0'])[0]} task(s) replaced, "
                f"{query.get('added', ['0'])[0]} new progress entries, "
                f"{query.get('merged', ['0'])[0]} merged into existing day(s).</div>"
            )
        return f"""
<h1 class="serif">Sync with Google Sheets</h1>
<p class="sub">One spreadsheet, three tabs: <code>team</code>, <code>lanes</code>, <code>progress</code>. Edit there, pull here.</p>
{banner}
<div class="panel cream">
<form method="post" action="/sync/set">
<p><strong>Spreadsheet ID</strong> (the long string in the sheet's URL):</p>
<p><input name="sheet_id" value="{esc(sheet_id)}" size="50" required>
&nbsp;<button type="submit">Save</button></p>
</form>
<form method="post" action="/sync/pull">
<input type="hidden" name="sheet_id" value="{esc(sheet_id)}">
<p><button type="submit">Pull from Google Sheets now</button></p>
</form>
</div>
<div class="panel">
<h3>Tab layout</h3>
<table>
<thead><tr><th>Tab</th><th>Columns</th></tr></thead>
<tbody>
<tr><td><code>team</code></td><td><code>from_date, to_date, total_hc, throughput_per_day</code> — date-ranged; leave dates empty for open-ended</td></tr>
<tr><td><code>lanes</code></td><td><code>data_type, purpose, batch, total_required, assigned_hc, status</code></td></tr>
<tr><td><code>progress</code></td><td><code>date, data_type, purpose, batch, items_completed</code></td></tr>
</tbody>
</table>
<p class="muted">Share the spreadsheet as &ldquo;Anyone with the link → Viewer&rdquo;. The tool only reads it.
Team and tasks are replaced from the sheet; progress is merged (items summed per date + task), so entries logged here are kept.</p>
</div>
"""


def _fmt(d: date | None) -> str:
    return d.isoformat() if d else "—"


def _split_lane_field(value: str) -> tuple[str, str, str]:
    parts = value.split("|")
    if len(parts) != 3:
        raise StoreError("invalid task selection")
    return parts[0], parts[1], parts[2]


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    store.init()  # make sure the CSV files exist before serving
    if os.environ.get("PLANNER_SYNC_ON_START") == "1":
        from .sheets import get_sheet_id, pull

        sheet_id = get_sheet_id(store)
        if sheet_id:
            try:
                result = pull(store, sheet_id)
                print(
                    f"synced from Google Sheet {sheet_id}: "
                    f"{result.team_rows} team rows, {result.lanes_replaced} lanes, "
                    f"{result.progress_added} progress entries added, "
                    f"{result.progress_updated} updated"
                )
            except Exception as exc:  # noqa: BLE001 - never block startup on sync
                print(f"sync-on-start skipped: {exc}")
        else:
            print("sync-on-start enabled but no sheet configured (set ANNOTATION_SHEET_ID)")
    server = ThreadingHTTPServer((host, port), PlannerHandler)
    server.store = store  # type: ignore[attr-defined]
    return server


def run(store: Store, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = make_server(store, host, port)
    print(f"Annotation planner UI: http://{host}:{port}  (data dir: {store.root.resolve()})")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
