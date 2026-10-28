import csv
import io
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from planner.planning import build_plan, plan_dates
from planner.store import (
    Lane,
    ProgressEntry,
    Store,
    StoreError,
    TeamRow,
    _ranges_overlap,
)

TODAY = date(2025, 1, 6)  # a Monday


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_csvs(self):
        for name in ("team", "lanes", "progress"):
            self.assertTrue((self.store.root / f"{name}.csv").exists())

    def test_team_rows_date_ranged_and_non_overlapping(self):
        # fully open-ended row covers everything
        self.store.add_team_row(TeamRow(None, None, 4.0, 40.0))
        self.assertEqual(self.store.team_on(TODAY).total_hc, 4.0)
        with self.assertRaises(StoreError):
            self.store.add_team_row(TeamRow(TODAY, None, 2.0, 30.0))  # overlaps
        # a bounded row does not conflict with a later open-ended row
        self.store.clear_team_rows()
        self.store.add_team_row(TeamRow(date(2025, 1, 1), date(2025, 1, 31), 4.0, 40.0))
        self.store.add_team_row(TeamRow(date(2025, 2, 1), None, 2.0, 30.0))
        self.assertEqual(self.store.team_on(date(2025, 1, 31)).total_hc, 4.0)
        self.assertEqual(self.store.team_on(date(2025, 2, 1)).total_hc, 2.0)
        self.assertIsNone(self.store.team_on(date(2024, 12, 31)))

    def test_team_row_validation(self):
        with self.assertRaises(StoreError):
            self.store.add_team_row(TeamRow(None, None, -1, 40))
        with self.assertRaises(StoreError):
            self.store.add_team_row(TeamRow(None, None, 4, 0))
        with self.assertRaises(StoreError):
            self.store.add_team_row(
                TeamRow(date(2025, 2, 1), date(2025, 1, 1), 4, 40)
            )

    def test_ranges_overlap_matrix(self):
        N = None
        d = date
        cases = [
            (TeamRow(N, N, 1, 1), TeamRow(d(2025, 1, 1), N, 1, 1), True),
            (TeamRow(N, d(2025, 1, 31), 1, 1), TeamRow(d(2025, 2, 1), N, 1, 1), False),
            (TeamRow(N, d(2025, 1, 31), 1, 1), TeamRow(d(2025, 1, 15), N, 1, 1), True),
            (TeamRow(d(2025, 2, 1), N, 1, 1), TeamRow(d(2025, 3, 1), d(2025, 3, 31), 1, 1), True),
            (TeamRow(d(2025, 3, 1), d(2025, 3, 31), 1, 1), TeamRow(d(2025, 2, 1), N, 1, 1), True),
            (TeamRow(d(2025, 1, 1), d(2025, 1, 31), 1, 1), TeamRow(d(2025, 2, 1), N, 1, 1), False),
            (TeamRow(d(2025, 3, 1), d(2025, 3, 31), 1, 1), TeamRow(N, d(2025, 1, 31), 1, 1), False),
            (TeamRow(N, d(2025, 1, 31), 1, 1), TeamRow(d(2025, 2, 1), d(2025, 2, 28), 1, 1), False),
            (TeamRow(d(2025, 1, 1), d(2025, 1, 31), 1, 1), TeamRow(N, d(2025, 1, 15), 1, 1), True),
            (TeamRow(d(2025, 1, 1), d(2025, 1, 31), 1, 1), TeamRow(d(2025, 1, 31), d(2025, 2, 28), 1, 1), True),
            (TeamRow(d(2025, 1, 1), d(2025, 1, 30), 1, 1), TeamRow(d(2025, 1, 31), d(2025, 2, 28), 1, 1), False),
            (TeamRow(N, d(2025, 1, 31), 1, 1), TeamRow(N, d(2025, 2, 28), 1, 1), True),
            (TeamRow(d(2025, 1, 1), N, 1, 1), TeamRow(d(2025, 2, 1), N, 1, 1), True),
        ]
        for a, b, expected in cases:
            self.assertIs(_ranges_overlap(a, b), expected, f"{a} vs {b}")
            self.assertIs(_ranges_overlap(b, a), expected, f"{b} vs {a}")

    def test_team_rows_open_then_bounded_are_rejected(self):
        # a forward-open row covers every later day, so a bounded row after
        # it is a genuine overlap and must be rejected
        self.store.add_team_row(TeamRow(date(2025, 2, 1), None, 2, 55))
        with self.assertRaises(StoreError):
            self.store.add_team_row(TeamRow(date(2025, 3, 1), date(2025, 3, 31), 3, 50))
        # a bounded row followed by a forward-open row starting the next day
        # is fine (no shared day)
        self.store.clear_team_rows()
        self.store.add_team_row(TeamRow(date(2025, 1, 1), date(2025, 1, 31), 4, 40))
        self.store.add_team_row(TeamRow(date(2025, 2, 1), None, 2, 55))
        self.assertEqual(len(self.store.list_team_rows()), 2)

    def test_replace_team_rows_rejects_overlap(self):
        with self.assertRaises(StoreError):
            self.store.replace_team_rows(
                [
                    TeamRow(None, None, 4, 40),
                    TeamRow(TODAY, None, 2, 30),
                ]
            )

    def test_lane_validation(self):
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Bad", "p", "B1", 10, 1.0, "not_started"))
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Eval", "p", "batch1", 10, 1.0, "not_started"))
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Eval", "", "B1", 10, 1.0, "not_started"))
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Eval", "p", "B1", -1, 1.0, "not_started"))
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Eval", "p", "B1", 10, -1.0, "not_started"))
        with self.assertRaises(StoreError):
            self.store.set_lane(Lane("Eval", "p", "B1", 10, 1.0, "weird"))

    def test_lane_upsert_and_status(self):
        self.store.set_lane(Lane("Eval", "qc", "B1", 100, 2.0, "not_started"))
        self.store.set_lane(Lane("Eval", "qc", "B1", 200, 1.0, "in_progress"))
        lanes = self.store.list_lanes()
        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0].total_required, 200)
        self.store.set_lane_status("Eval", "qc", "B1", "stopped")
        self.assertEqual(self.store.get_lane("Eval", "qc", "B1").status, "stopped")

    def test_progress_accumulates_and_validates(self):
        with self.assertRaises(StoreError):
            self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 0))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 20))
        entries = self.store.list_progress()
        self.assertEqual(sum(e.items_completed for e in entries), 50)

    def test_progress_accepts_sheets_datetime(self):
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 5))
        self.store.merge_progress(
            [ProgressEntry(TODAY, "Eval", "qc", "B1", 10)]
        )
        entries = self.store.list_progress()
        self.assertEqual(sum(e.items_completed for e in entries), 15)


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()
        self.store.add_team_row(TeamRow(None, None, 4.0, 40.0))

    def tearDown(self):
        self.tmp.cleanup()

    def _lane(self, dt="Eval", pu="qc", ba="B1", req=100, hc=1.0, st="in_progress"):
        self.store.set_lane(Lane(dt, pu, ba, req, hc, st))

    def test_capacity_is_hc_times_throughput(self):
        self._lane(req=160, hc=2.0)
        report = build_plan(self.store, TODAY)
        lp = report.lanes[0]
        self.assertAlmostEqual(lp.capacity_per_day, 80.0)  # 2 HC * 40
        self.assertEqual(lp.start_date, TODAY)
        self.assertEqual(lp.finish_date, date(2025, 1, 7))  # 160/80 = 2 days

    def test_per_day_throughput_changes_schedule(self):
        # 40/day until Jan 9, then 80/day (via 2x throughput) from Jan 10
        self.store.replace_team_rows(
            [
                TeamRow(None, date(2025, 1, 9), 1.0, 40.0),
                TeamRow(date(2025, 1, 10), None, 1.0, 80.0),
            ]
        )
        self._lane(req=200, hc=1.0)
        report = build_plan(self.store, TODAY)
        lp = report.lanes[0]
        # Jan 6-9: 4 days * 40 = 160; remaining 40 at 80/day -> 1 day (Jan 10)
        self.assertEqual(lp.start_date, TODAY)
        self.assertEqual(lp.finish_date, date(2025, 1, 10))

    def test_gap_in_team_rows_pauses_schedule(self):
        # HC only in the first week; nothing the week after
        self.store.replace_team_rows(
            [TeamRow(TODAY, date(2025, 1, 10), 1.0, 40.0)]
        )
        self._lane(req=1000, hc=1.0)
        report = build_plan(self.store, TODAY)
        lp = report.lanes[0]
        # 5 days * 40 = 200 done by Jan 10, then no capacity; finish is None
        self.assertIsNone(lp.finish_date)
        self.assertTrue(any("could not finish" in w for w in report.warnings))

    def test_snapshot_counts(self):
        self._lane("Eval", "qc", "B1", 100, 1.0, "in_progress")
        self._lane("FT", "train", "B1", 50, 1.0, "in_progress")
        self._lane("Test", "smoke", "B1", 25, 0.5, "not_started")
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 40))
        report = build_plan(self.store, TODAY)
        s = report.snapshot
        self.assertEqual(s.total_required, 175)
        self.assertEqual(s.total_completed, 40)
        self.assertEqual(s.total_in_progress, 110)

    def test_batches_run_sequentially_within_type_purpose(self):
        self._lane("FT", "train", "B1", 40, 1.0)
        self._lane("FT", "train", "B2", 40, 1.0)
        report = build_plan(self.store, TODAY)
        b1, b2 = report.lanes
        self.assertEqual(b1.finish_date, TODAY)  # 40 items at 40/day = 1 day
        self.assertEqual(b2.start_date, date(2025, 1, 7))

    def test_groups_run_in_parallel(self):
        self._lane("Eval", "qc", "B1", 40, 1.0)
        self._lane("Test", "smoke", "B1", 40, 1.0)
        report = build_plan(self.store, TODAY)
        self.assertEqual(len(report.lanes), 2)
        for lp in report.lanes:
            self.assertEqual(lp.start_date, TODAY)

    def test_progress_shifts_schedule(self):
        self._lane(req=100, hc=1.0)
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 60))
        report = build_plan(self.store, TODAY)
        lp = report.lanes[0]
        self.assertEqual(lp.completed, 60)
        self.assertEqual(lp.remaining, 40)
        self.assertEqual(lp.finish_date, TODAY)  # 40 remaining at 40/day

    def test_stopped_and_completed_lanes_not_scheduled(self):
        self._lane("Eval", "qc", "B1", 40, 1.0, "stopped")
        self._lane("Eval", "qc", "B2", 40, 1.0, "completed")
        report = build_plan(self.store, TODAY)
        for lp in report.lanes:
            self.assertIsNone(lp.start_date)
            self.assertIsNone(lp.finish_date)

    def test_no_team_row_warns(self):
        self.store.clear_team_rows()
        self._lane("Eval", "qc", "B1", 40, 1.0)
        report = build_plan(self.store, TODAY)
        self.assertTrue(any("no team rows" in w for w in report.warnings))
        self.assertIsNone(report.lanes[0].start_date)

    def test_over_assigned_hc_warning(self):
        self.store.replace_team_rows([TeamRow(None, None, 1.0, 40.0)])
        self._lane("Eval", "qc", "B1", 40, 1.0)
        self._lane("FT", "train", "B1", 40, 1.0)
        report = build_plan(self.store, TODAY)
        self.assertTrue(any("exceeds peak total HC" in w for w in report.warnings))

    def test_plan_dates_covers_progress_and_schedule(self):
        self._lane(req=80, hc=1.0)
        self.store.log_progress(ProgressEntry(date(2025, 1, 3), "Eval", "qc", "B1", 10))
        report = build_plan(self.store, TODAY)
        dates = plan_dates(report)
        self.assertIn(date(2025, 1, 3), dates)
        self.assertIn(TODAY, dates)
        self.assertIn(date(2025, 1, 7), dates)  # 80/40 = 2 days

    def test_daily_grid_aggregates_by_day(self):
        self._lane(req=100, hc=1.0)
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 10))
        self.store.log_progress(ProgressEntry(date(2025, 1, 7), "Eval", "qc", "B1", 5))
        report = build_plan(self.store, TODAY)
        from planner.planning import LaneKey

        key = LaneKey("Eval", "qc", "B1")
        self.assertEqual(report.daily[TODAY][key], 40)
        self.assertEqual(report.daily[date(2025, 1, 7)][key], 5)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebTest(unittest.TestCase):
    def setUp(self):
        import threading

        from planner.web import make_server

        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()
        self.server = make_server(self.store, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}"
        self.opener = urllib.request.build_opener(NoRedirect())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.url + path) as resp:
            return resp.status, resp.read().decode()

    def _post(self, path, data):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(self.url + path, data=body)
        try:
            with self.opener.open(req) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_dashboard_renders_branded_snapshot_and_table(self):
        self.store.add_team_row(TeamRow(None, None, 4.0, 40.0))
        self.store.set_lane(Lane("Eval", "qc", "B1", 100, 2.0, "in_progress"))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 40))
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Total required", body)
        self.assertIn("Peak team HC", body)
        self.assertIn("Assigned HC", body)
        self.assertIn("Capacity/day", body)
        self.assertIn("Daily completions", body)
        # Mistral brand markers
        self.assertIn("--primary:#fa520f", body)
        self.assertIn("sunset stripe", body.replace('class="stripe"', "sunset stripe"))
        self.assertIn("Eval", body)

    def test_team_row_roundtrip_via_web(self):
        status, _ = self._post(
            "/team/add",
            {
                "from_date": "2025-01-06",
                "to_date": "2025-01-31",
                "total_hc": "3",
                "throughput_per_day": "25",
            },
        )
        self.assertEqual(status, 303)
        row = self.store.list_team_rows()[0]
        self.assertEqual(row.total_hc, 3.0)
        self.assertEqual(row.throughput_per_day, 25.0)
        self.assertEqual(row.from_date, date(2025, 1, 6))

    def test_team_overlap_rejected_via_web(self):
        self.store.add_team_row(TeamRow(None, None, 4.0, 40.0))
        status, body = self._post(
            "/team/add",
            {"from_date": "", "to_date": "", "total_hc": "2", "throughput_per_day": "30"},
        )
        self.assertEqual(status, 200)
        self.assertIn("overlaps", body)

    def test_lane_add_update_delete_via_web(self):
        self._post(
            "/lanes/add",
            {
                "data_type": "FT",
                "purpose": "train",
                "batch": "B1",
                "total_required": "150",
                "assigned_hc": "1.5",
                "status": "not_started",
            },
        )
        lane = self.store.get_lane("FT", "train", "B1")
        self.assertEqual(lane.total_required, 150)
        self._post(
            "/lanes/update",
            {
                "data_type": "FT",
                "purpose": "train",
                "batch": "B1",
                "total_required": "200",
                "assigned_hc": "2",
                "status": "in_progress",
            },
        )
        lane = self.store.get_lane("FT", "train", "B1")
        self.assertEqual(lane.total_required, 200)
        self.assertEqual(lane.status, "in_progress")
        self._post("/lanes/delete", {"data_type": "FT", "purpose": "train", "batch": "B1"})
        self.assertEqual(self.store.list_lanes(), [])

    def test_progress_log_via_web(self):
        self.store.set_lane(Lane("Eval", "qc", "B1", 100, 1.0, "in_progress"))
        self._post(
            "/progress/add",
            {"date": "2025-01-06", "lane": "Eval|qc|B1", "items": "35"},
        )
        entries = self.store.list_progress()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].items_completed, 35)

    def test_sync_page_single_sheet_form(self):
        status, body = self._get("/sync")
        self.assertEqual(status, 200)
        self.assertIn("One spreadsheet, three tabs", body)
        self.assertIn("Pull from Google Sheets now", body)
        status, _ = self._post("/sync/set", {"sheet_id": "abc123"})
        self.assertEqual(status, 303)
        from planner.sheets import get_sheet_id

        self.assertEqual(get_sheet_id(self.store), "abc123")

    def test_invalid_post_shows_error_not_crash(self):
        status, body = self._post(
            "/lanes/add",
            {
                "data_type": "Nope",
                "purpose": "x",
                "batch": "B1",
                "total_required": "1",
                "assigned_hc": "1",
                "status": "not_started",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("invalid data_type", body)


class SheetsSyncTest(unittest.TestCase):
    """Single-sheet sync tested against a local HTTP server standing in for
    the Google Sheets CSV export."""

    def setUp(self):
        import http.server
        import threading

        from planner import sheets

        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()

        team_csv = (
            "from_date,to_date,total_hc,throughput_per_day\r\n"
            ",2025-01-31,4,40\r\n"
            "2025-02-01,,2,50\r\n"
        )
        lanes_csv = (
            "data_type,purpose,batch,total_required,assigned_hc,status\r\n"
            "Eval,qc,B1,100,2,in_progress\r\n"
            "Test,smoke,B1,50,1,not_started\r\n"
        )
        progress_csv = (
            "date,data_type,purpose,batch,items_completed\r\n"
            "2025-01-06,Eval,qc,B1,40\r\n"
        )
        tabs = {"team": team_csv, "lanes": lanes_csv, "progress": progress_csv}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                tab = qs.get("sheet", [""])[0]
                if tab in tabs:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv")
                    self.end_headers()
                    self.wfile.write(tabs[tab].encode())
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        self._orig_fetch = sheets._fetch_tab_csv
        base = f"http://127.0.0.1:{self.port}"
        sheets._fetch_tab_csv = lambda sheet_id, tab: list(
            csv.DictReader(io.StringIO(self._http_get(f"{base}/export?sheet={tab}")))
        )

    def _http_get(self, url):
        with urllib.request.urlopen(url) as r:
            return r.read().decode()

    def tearDown(self):
        from planner import sheets

        sheets._fetch_tab_csv = self._orig_fetch
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_pull_single_sheet_replaces_and_merges(self):
        from planner.sheets import pull, set_sheet_id

        # pre-existing local progress on the same day must be summed, not lost
        self.store.log_progress(ProgressEntry(date(2025, 1, 6), "Eval", "qc", "B1", 10))

        set_sheet_id(self.store, "localsheet")
        result = pull(self.store)

        self.assertEqual(result.team_rows, 2)
        self.assertEqual(result.lanes_replaced, 2)
        self.assertEqual(result.progress_added, 0)
        self.assertEqual(result.progress_updated, 1)

        rows = self.store.list_team_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].total_hc, 4.0)
        self.assertEqual(rows[0].to_date, date(2025, 1, 31))
        self.assertEqual(rows[1].from_date, date(2025, 2, 1))

        lanes = {(l.data_type, l.purpose, l.batch): l for l in self.store.list_lanes()}
        self.assertEqual(len(lanes), 2)
        self.assertEqual(lanes[("Eval", "qc", "B1")].total_required, 100)

        entries = self.store.list_progress()
        self.assertEqual(sum(e.items_completed for e in entries), 50)  # 10 + 40

    def test_pull_requires_sheet_id(self):
        from planner.sheets import pull

        with self.assertRaises(StoreError):
            pull(self.store)


if __name__ == "__main__":
    unittest.main()
