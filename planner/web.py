"""Zero-dependency web UI for the annotation planner.

Serves a dashboard (snapshot + breakdown table + daily grid) and edit forms
for team settings, lanes, and progress. Built on http.server; all state lives
in the CSV files, so the CLI and the UI stay in sync.

Run:  python3 -m planner.cli --dir data web --port 8000
"""

from __future__ import annotations

import html
import re
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
    Team,
    parse_date,
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Annotation Planner</title>
<style>
 body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem; color: #1a1a1a; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; margin: 0.5rem 0; font-size: 0.85rem; }}
 th, td {{ border: 1px solid #ccc; padding: 0.3rem 0.5rem; text-align: left; }}
 th {{ background: #f0f0f0; }}
 td.num, th.num {{ text-align: right; }}
 .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
 .card {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem 1.5rem; min-width: 10rem; }}
 .card .big {{ font-size: 1.6rem; font-weight: 600; }}
 .warn {{ color: #a00; }}
 form.inline {{ display: inline; }}
 input, select {{ font-size: 0.85rem; padding: 0.15rem 0.3rem; }}
 input[type=number] {{ width: 6rem; }}
 .grid td {{ min-width: 3.2rem; text-align: right; }}
 .grid td.rowlabel {{ min-width: 12rem; text-align: left; }}
 nav a {{ margin-right: 1rem; }}
 .flash {{ background: #e6f4e6; border: 1px solid #9c9; padding: 0.5rem 1rem; border-radius: 6px; }}
 .err {{ background: #fbe6e6; border: 1px solid #c99; padding: 0.5rem 1rem; border-radius: 6px; }}
</style>
</head>
<body>
<nav><a href="/">Dashboard</a><a href="/team">Team &amp; throughput</a><a href="/lanes">Tasks (lanes)</a><a href="/progress">Log progress</a></nav>
{flash}
{body}
</body>
</html>
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _options(values: list[str], selected: str) -> str:
    return "".join(
        f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(v)}</option>'
        for v in values
    )


class PlannerHandler(BaseHTTPRequestHandler):
    store: Store = None  # set on the server instance
    server_version = "AnnotationPlanner/1.0"

    def log_message(self, format: str, *args: object) -> None:
        pass

    # ----- helpers ---------------------------------------------------

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

    def _page(self, body: str, flash: str = "", error: str = "") -> None:
        flash_html = ""
        if flash:
            flash_html = f'<div class="flash">{esc(flash)}</div>'
        if error:
            flash_html += f'<div class="err">{esc(error)}</div>'
        self._send(_PAGE.format(flash=flash_html, body=body))

    def _redirect(self, location: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    # ----- routing ---------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._page(self._dashboard())
            elif path == "/team":
                self._page(self._team_form())
            elif path == "/lanes":
                self._page(self._lanes_page())
            elif path == "/progress":
                self._page(self._progress_page())
            else:
                self._send("not found", 404)
        except StoreError as exc:
            self._page(f"<h1>Error</h1><p>{esc(exc)}</p>", error=str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        get = lambda k, d="": form.get(k, [d])[0].strip()
        try:
            if path == "/team":
                self.st.set_team(
                    Team(
                        total_hc=float(get("total_hc", "0")),
                        throughput_per_day=float(get("throughput_per_day", "0")),
                    )
                )
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
            else:
                self._send("not found", 404)
        except (StoreError, ValueError) as exc:
            self._page(f"<h1>Could not save</h1><p>{esc(exc)}</p>", error=str(exc))

    # ----- pages -----------------------------------------------------

    def _dashboard(self) -> str:
        today = date.today()
        report = build_plan(self.st, today)
        team = report.team
        snap = report.snapshot

        cards = f"""
<div class="cards">
  <div class="card"><div>Total required</div><div class="big">{snap.total_required}</div></div>
  <div class="card"><div>Total completed</div><div class="big">{snap.total_completed}</div></div>
  <div class="card"><div>In progress (remaining)</div><div class="big">{snap.total_in_progress}</div></div>
  <div class="card"><div>Overall %</div><div class="big">{snap.pct_complete:.1f}%</div></div>
  <div class="card"><div>Total HC</div><div class="big">{team.total_hc:g}</div></div>
  <div class="card"><div>Throughput (items/HC/day)</div><div class="big">{team.throughput_per_day:g}</div></div>
</div>
"""

        rows = []
        for lp in report.lanes:
            lane = lp.lane
            rows.append(
                "<tr>"
                f"<td>{esc(lane.data_type)}</td>"
                f"<td>{esc(lane.purpose)}</td>"
                f"<td>{esc(lane.batch)}</td>"
                f'<td class="num">{lane.total_required}</td>'
                f'<td class="num">{lp.completed}</td>'
                f'<td class="num">{lp.pct_complete:.1f}%</td>'
                f'<td class="num">{lane.assigned_hc:g}</td>'
                f'<td class="num">{lp.capacity_per_day:.1f}</td>'
                f"<td>{esc(lane.status)}</td>"
                f"<td>{lp.start_date.isoformat() if lp.start_date else '-'}</td>"
                f"<td>{lp.finish_date.isoformat() if lp.finish_date else '-'}</td>"
                "</tr>"
            )
        table = f"""
<table>
<tr><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Required</th>
<th class="num">Completed</th><th class="num">%</th>
<th class="num">Assigned HC</th><th class="num">Capacity/day</th>
<th>Status</th><th>Start</th><th>Finish</th></tr>
{''.join(rows) if rows else '<tr><td colspan="11">No tasks yet — add them under Tasks (lanes).</td></tr>'}
</table>
"""

        dates = plan_dates(report)
        grid = ""
        if dates:
            header = "".join(f"<th>{d.isoformat()[5:]}</th>" for d in dates)
            body_rows = []
            for lp in report.lanes:
                lane = lp.lane
                key_label = f"{lane.data_type}/{lane.purpose}/{lane.batch}"
                cells = []
                for d in dates:
                    val = report.daily.get(d, {})
                    from .planning import LaneKey

                    n = val.get(LaneKey(lane.data_type, lane.purpose, lane.batch), 0)
                    cells.append(f'<td class="num">{"" if n == 0 else n}</td>')
                body_rows.append(
                    f'<tr><td class="rowlabel">{esc(key_label)}</td>{"".join(cells)}</tr>'
                )
            grid = f"""
<h2>Daily completions (columns = dates)</h2>
<table class="grid">
<tr><th>Lane</th>{header}</tr>
{''.join(body_rows)}
</table>
"""

        warnings = ""
        if report.warnings:
            items = "".join(f"<li>{esc(w)}</li>" for w in report.warnings)
            warnings = f'<h2 class="warn">Warnings</h2><ul class="warn">{items}</ul>'

        return f"<h1>Dashboard (as of {today.isoformat()})</h1>{cards}<h2>Task breakdown</h2>{table}{grid}{warnings}"

    def _team_form(self) -> str:
        team = self.st.get_team()
        return f"""
<h1>Team &amp; throughput</h1>
<form method="post" action="/team">
<table>
<tr><th>Total HC (operators)</th><td><input type="number" step="0.5" min="0" name="total_hc" value="{team.total_hc:g}" required></td></tr>
<tr><th>Throughput (items per HC per day)</th><td><input type="number" step="0.1" min="0.1" name="throughput_per_day" value="{team.throughput_per_day:g}" required></td></tr>
</table>
<p><button type="submit">Save</button></p>
</form>
<p>Capacity for a task = assigned HC &times; throughput. Example: 2 HC &times; 40 = 80 items/day.</p>
"""

    def _lanes_page(self) -> str:
        rows = []
        for lane in self.st.list_lanes():
            rows.append(
                "<tr>"
                f'<td><form class="inline" method="post" action="/lanes/update">'
                f'<input type="hidden" name="data_type" value="{esc(lane.data_type)}">'
                f'<input type="hidden" name="purpose" value="{esc(lane.purpose)}">'
                f'<input type="hidden" name="batch" value="{esc(lane.batch)}">'
                f'{esc(lane.data_type)}</td>'
                f'<td>{esc(lane.purpose)}</td>'
                f"<td>{esc(lane.batch)}</td>"
                f'<td class="num"><input type="number" min="0" name="total_required" value="{lane.total_required}"></td>'
                f'<td class="num"><input type="number" step="0.5" min="0" name="assigned_hc" value="{lane.assigned_hc:g}"></td>'
                f'<td><select name="status">{_options(list(STATUSES), lane.status)}</select></td>'
                f'<td><button type="submit">Save</button> '
                f'<button type="submit" formaction="/lanes/delete">Delete</button></form></td>'
                "</tr>"
            )
        table = f"""
<table>
<tr><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Required</th>
<th class="num">Assigned HC</th><th>Status</th><th></th></tr>
{''.join(rows)}
<tr>
<form method="post" action="/lanes/add">
<td><select name="data_type">{_options(list(DATA_TYPES), "Eval")}</select></td>
<td><input name="purpose" placeholder="e.g. qc" required></td>
<td><input name="batch" placeholder="B1" required></td>
<td class="num"><input type="number" min="0" name="total_required" value="0"></td>
<td class="num"><input type="number" step="0.5" min="0" name="assigned_hc" value="0"></td>
<td><select name="status">{_options(list(STATUSES), "not_started")}</select></td>
<td><button type="submit">Add task</button></td>
</form>
</tr>
</table>
"""
        return f"<h1>Tasks (lanes)</h1><p>Each row is one labeling task: a batch broken down by data type and purpose.</p>{table}"

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
<table>
<tr><th>Date</th><th>Type</th><th>Purpose</th><th>Batch</th><th class="num">Items</th></tr>
{''.join(entries) if entries else '<tr><td colspan="5">Nothing logged yet.</td></tr>'}
</table>
"""
        return f"""
<h1>Log completed work</h1>
<form method="post" action="/progress/add">
<table>
<tr><th>Date (YYYY-MM-DD)</th><td><input name="date" placeholder="2025-01-08" required></td></tr>
<tr><th>Task</th><td><select name="lane">{lane_opts}</select></td></tr>
<tr><th>Items completed</th><td><input type="number" min="1" name="items" value="1" required></td></tr>
</table>
<p><button type="submit">Log</button></p>
</form>
<p>Tip: the date field also accepts pasting a batch from a spreadsheet.</p>
<h2>History</h2>
{log}
"""


def _split_lane_field(value: str) -> tuple[str, str, str]:
    parts = value.split("|")
    if len(parts) != 3:
        raise StoreError("invalid task selection")
    return parts[0], parts[1], parts[2]


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
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
