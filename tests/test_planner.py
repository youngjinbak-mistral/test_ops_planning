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
from planner.store import Lane, ProgressEntry, Store, StoreError, Team

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

    def test_team_roundtrip_and_validation(self):
        self.assertEqual(self.store.get_team(), Team(0.0, 0.0))
        self.store.set_team(Team(4.0, 40.0))
        self.assertEqual(self.store.get_team(), Team(4.0, 40.0))
        with self.assertRaises(StoreError):
            self.store.set_team(Team(-1, 40))
        with self.assertRaises(StoreError):
            self.store.set_team(Team(4, 0))

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
        self.assertEqual(lanes[0].assigned_hc, 1.0)
        self.assertEqual(lanes[0].status, "in_progress")
        self.store.set_lane_status("Eval", "qc", "B1", "stopped")
        self.assertEqual(self.store.get_lane("Eval", "qc", "B1").status, "stopped")

    def test_progress_accumulates_and_validates(self):
        with self.assertRaises(StoreError):
            self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 0))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 20))
        entries = self.store.list_progress()
        self.assertEqual(sum(e.items_completed for e in entries), 50)


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()
        self.store.set_team(Team(4.0, 40.0))

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

    def test_snapshot_counts(self):
        self._lane("Eval", "qc", "B1", 100, 1.0, "in_progress")
        self._lane("FT", "train", "B1", 50, 1.0, "in_progress")
        self._lane("Test", "smoke", "B1", 25, 0.5, "not_started")
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 40))
        report = build_plan(self.store, TODAY)
        s = report.snapshot
        self.assertEqual(s.total_required, 175)
        self.assertEqual(s.total_completed, 40)
        # in_progress lanes: Eval remaining 60 + FT remaining 50
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

    def test_no_hc_warns_and_blocks_later_batches(self):
        self._lane("Eval", "qc", "B1", 40, 0.0)
        self._lane("Eval", "qc", "B2", 40, 1.0)
        report = build_plan(self.store, TODAY)
        b1, b2 = report.lanes
        self.assertIsNone(b1.start_date)
        self.assertIsNone(b2.start_date)  # blocked behind unscheduled B1
        self.assertTrue(any("no HC assigned" in w for w in report.warnings))

    def test_over_assigned_hc_warning(self):
        self.store.set_team(Team(1.0, 40.0))
        self._lane("Eval", "qc", "B1", 40, 1.0)
        self._lane("FT", "train", "B1", 40, 1.0)
        report = build_plan(self.store, TODAY)
        self.assertTrue(any("exceeds total HC" in w for w in report.warnings))

    def test_plan_dates_covers_progress_and_schedule(self):
        self._lane(req=80, hc=1.0)
        self.store.log_progress(ProgressEntry(date(2025, 1, 3), "Eval", "qc", "B1", 10))
        report = build_plan(self.store, TODAY)
        dates = plan_dates(report)
        self.assertIn(date(2025, 1, 3), dates)   # logged day
        self.assertIn(TODAY, dates)               # schedule start
        self.assertIn(date(2025, 1, 7), dates)    # schedule finish (80/40=2d)

    def test_daily_grid_aggregates_by_day(self):
        self._lane(req=100, hc=1.0)
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 10))
        self.store.log_progress(
            ProgressEntry(date(2025, 1, 7), "Eval", "qc", "B1", 5)
        )
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
        import urllib.parse

        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(self.url + path, data=body)
        try:
            with self.opener.open(req) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_dashboard_renders_snapshot_and_table(self):
        self.store.set_team(Team(4.0, 40.0))
        self.store.set_lane(Lane("Eval", "qc", "B1", 100, 2.0, "in_progress"))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 40))
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Total required", body)
        self.assertIn("Total completed", body)
        self.assertIn("In progress", body)
        self.assertIn("Eval", body)
        self.assertIn("qc", body)
        self.assertIn("Assigned HC", body)
        self.assertIn("Capacity/day", body)
        # daily grid columns exist (logged date)
        self.assertIn("Daily completions", body)
        self.assertIn(TODAY.isoformat()[5:], body)

    def test_team_form_roundtrip(self):
        status, _ = self._post(
            "/team", {"total_hc": "3", "throughput_per_day": "25"}
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.store.get_team(), Team(3.0, 25.0))

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
        self.assertEqual(lane.assigned_hc, 1.5)
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
        self._post(
            "/lanes/delete",
            {"data_type": "FT", "purpose": "train", "batch": "B1"},
        )
        self.assertEqual(self.store.list_lanes(), [])

    def test_progress_log_via_web(self):
        self.store.set_lane(Lane("Eval", "qc", "B1", 100, 1.0, "in_progress"))
        self._post(
            "/progress/add",
            {
                "date": "2025-01-06",
                "lane": "Eval|qc|B1",
                "items": "35",
            },
        )
        entries = self.store.list_progress()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].items_completed, 35)

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

    def test_sync_page_renders(self):
        status, body = self._get("/sync")
        self.assertEqual(status, 200)
        self.assertIn("Sync with Google Sheets", body)
        self.assertIn("Pull from Google Sheets now", body)

    def test_sync_set_prefix_via_web(self):
        status, _ = self._post("/sync/set", {"prefix": "My Planner"})
        self.assertEqual(status, 303)
        from planner.sheets import get_sheet_prefix

        self.assertEqual(get_sheet_prefix(self.store), "My Planner")


class SheetsSyncTest(unittest.TestCase):
    """Sync logic tested against a local HTTP server standing in for Drive."""

    def setUp(self):
        import http.server
        import threading

        from planner import sheets

        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()

        team_csv = "total_hc,throughput_per_day\r\n5,30\r\n"
        lanes_csv = (
            "data_type,purpose,batch,total_required,assigned_hc,status\r\n"
            "Eval,qc,B1,100,2,in_progress\r\n"
            "Test,smoke,B1,50,1,not_started\r\n"
        )
        progress_csv = (
            "date,data_type,purpose,batch,items_completed\r\n"
            "2025-01-06,Eval,qc,B1,40\r\n"
        )

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/drive/files"):
                    import urllib.parse

                    q = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query
                    ).get("q", [""])[0]
                    if "team" in q:
                        body = json.dumps({"files": [{"id": "team123"}]})
                    elif "lanes" in q:
                        body = json.dumps({"files": [{"id": "lanes456"}]})
                    elif "progress" in q:
                        body = json.dumps({"files": [{"id": "prog789"}]})
                    else:
                        body = json.dumps({"files": []})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body.encode())
                elif self.path.startswith("/spreadsheets/d/team123"):
                    self._csv(team_csv)
                elif self.path.startswith("/spreadsheets/d/lanes456"):
                    self._csv(lanes_csv)
                elif self.path.startswith("/spreadsheets/d/prog789"):
                    self._csv(progress_csv)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _csv(self, text):
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.end_headers()
                self.wfile.write(text.encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        # point sheets.py at the local server by pre-seeding exact sheet IDs
        self._base = f"http://127.0.0.1:{self.port}"
        self._orig_fetch = sheets._fetch_csv_by_id
        sheets._fetch_csv_by_id = lambda sheet_id: list(
            csv.DictReader(io.StringIO(self._http_get(f"{self._base}/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv")))
        )
        # seed exact IDs so no Drive search is needed
        sheets.set_sheet_id(self.store, "team", "team123")
        sheets.set_sheet_id(self.store, "lanes", "lanes456")
        sheets.set_sheet_id(self.store, "progress", "prog789")

    def _http_get(self, url):
        with urllib.request.urlopen(url) as r:
            return r.read().decode()

    def tearDown(self):
        from planner import sheets

        sheets._fetch_csv_by_id = self._orig_fetch
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_pull_replaces_team_and_lanes_merges_progress(self):
        from planner.sheets import pull, set_sheet_prefix

        # pre-existing local progress on the same day must be summed, not lost
        self.store.log_progress(ProgressEntry(date(2025, 1, 6), "Eval", "qc", "B1", 10))

        set_sheet_prefix(self.store, "TestPlanner")
        result = pull(self.store)  # IDs seeded in setUp take precedence

        self.assertTrue(result.team_synced)
        self.assertEqual(result.lanes_replaced, 2)
        self.assertEqual(result.progress_added, 0)
        self.assertEqual(result.progress_updated, 1)

        self.assertEqual(self.store.get_team(), Team(5.0, 30.0))
        lanes = {(l.data_type, l.purpose, l.batch): l for l in self.store.list_lanes()}
        self.assertEqual(len(lanes), 2)
        self.assertEqual(lanes[("Eval", "qc", "B1")].total_required, 100)

        entries = self.store.list_progress()
        total = sum(e.items_completed for e in entries)
        self.assertEqual(total, 50)  # 10 local + 40 from sheet, merged on same day

    def test_pull_requires_config(self):
        from planner.sheets import pull

        fresh = Store(Path(self.tmp.name) / "fresh")
        fresh.init()
        with self.assertRaises(StoreError):
            pull(fresh)


if __name__ == "__main__":
    unittest.main()
