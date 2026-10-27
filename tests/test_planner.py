import csv
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from planner.planning import build_plan, completion_summary
from planner.store import (
    Allocation,
    Operator,
    ProgressEntry,
    Requirement,
    Store,
    StoreError,
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
        for name in ("operators", "requirements", "allocations", "progress"):
            self.assertTrue((self.store.root / f"{name}.csv").exists())

    def test_operators_roundtrip_and_edit(self):
        self.store.upsert_operator(Operator("op1", "Alice", 40.0, True))
        self.store.upsert_operator(Operator("op2", "Bob", 25.5, True))
        ops = {op.operator_id: op for op in self.store.list_operators()}
        self.assertEqual(ops["op1"].throughput_per_day, 40.0)
        self.store.upsert_operator(Operator("op1", "Alice", 50.0, False))
        ops = {op.operator_id: op for op in self.store.list_operators()}
        self.assertEqual(ops["op1"].throughput_per_day, 50.0)
        self.assertFalse(ops["op1"].active)
        self.assertEqual(len(ops), 2)

    def test_requirement_validation(self):
        with self.assertRaises(StoreError):
            self.store.set_requirement(Requirement("Bad", "p", "B1", 10))
        with self.assertRaises(StoreError):
            self.store.set_requirement(Requirement("Eval", "p", "batch1", 10))
        with self.assertRaises(StoreError):
            self.store.set_requirement(Requirement("Eval", "", "B1", 10))
        with self.assertRaises(StoreError):
            self.store.set_requirement(Requirement("Eval", "p", "B1", -1))

    def test_requirement_set_is_idempotent_upsert(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 100))
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 200))
        reqs = self.store.list_requirements()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].total_required, 200)

    def test_allocation_validates_operator_and_fraction(self):
        self.store.upsert_operator(Operator("op1", "Alice", 40.0, True))
        with self.assertRaises(StoreError):
            self.store.set_allocation(
                Allocation("Eval", "qc", "B1", "ghost", 0.5, None, None)
            )
        with self.assertRaises(StoreError):
            self.store.set_allocation(
                Allocation("Eval", "qc", "B1", "op1", 0.0, None, None)
            )
        with self.assertRaises(StoreError):
            self.store.set_allocation(
                Allocation("Eval", "qc", "B1", "op1", 1.5, None, None)
            )
        with self.assertRaises(StoreError):
            self.store.set_allocation(
                Allocation(
                    "Eval",
                    "qc",
                    "B1",
                    "op1",
                    0.5,
                    date(2025, 2, 1),
                    date(2025, 1, 1),
                )
            )

    def test_progress_accumulates_and_validates(self):
        with self.assertRaises(StoreError):
            self.store.log_progress(
                ProgressEntry(TODAY, "Eval", "qc", "B1", 0)
            )
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(
            ProgressEntry(TODAY, "Eval", "qc", "B1", 20)
        )
        entries = self.store.list_progress()
        self.assertEqual(len(entries), 2)
        self.assertEqual(sum(e.items_completed for e in entries), 50)

    def test_csv_is_editable_directly(self):
        self.store.upsert_operator(Operator("op1", "Alice", 40.0, True))
        path = self.store.root / "operators.csv"
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["throughput_per_day"], "40.0")
        rows[0]["throughput_per_day"] = "55"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["operator_id", "name", "throughput_per_day", "active"]
            )
            writer.writeheader()
            writer.writerows(rows)
        op = self.store.get_operator("op1")
        self.assertEqual(op.throughput_per_day, 55.0)


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.init()
        self.store.upsert_operator(Operator("op1", "Alice", 40.0, True))
        self.store.upsert_operator(Operator("op2", "Bob", 20.0, True))

    def tearDown(self):
        self.tmp.cleanup()

    def test_capacity_is_fraction_times_throughput(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 100))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 0.5, None, None))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op2", 1.0, None, None))
        lanes, _, warnings = build_plan(self.store, TODAY)
        batch = lanes[0].batches[0]
        # 0.5*40 + 1.0*20 = 40 items/day -> 100 items in 3 days (ceil)
        self.assertAlmostEqual(batch.capacity_per_day, 40.0)
        self.assertEqual(batch.start_date, TODAY)
        self.assertEqual(batch.finish_date, date(2025, 1, 8))
        self.assertEqual(warnings, [])

    def test_batches_run_sequentially_within_lane(self):
        self.store.set_requirement(Requirement("FT", "train", "B1", 40))
        self.store.set_requirement(Requirement("FT", "train", "B2", 40))
        self.store.set_allocation(Allocation("FT", "train", "B1", "op1", 1.0, None, None))
        self.store.set_allocation(Allocation("FT", "train", "B2", "op1", 1.0, None, None))
        lanes, _, _ = build_plan(self.store, TODAY)
        b1, b2 = lanes[0].batches
        self.assertEqual(b1.start_date, TODAY)
        self.assertEqual(b1.finish_date, TODAY)  # 40 items at 40/day = 1 day
        self.assertEqual(b2.start_date, date(2025, 1, 7))
        self.assertEqual(b2.finish_date, date(2025, 1, 7))

    def test_lanes_run_in_parallel(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 40))
        self.store.set_requirement(Requirement("Test", "smoke", "B1", 20))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 1.0, None, None))
        self.store.set_allocation(Allocation("Test", "smoke", "B1", "op2", 1.0, None, None))
        lanes, _, _ = build_plan(self.store, TODAY)
        self.assertEqual(len(lanes), 2)
        for lane in lanes:
            self.assertEqual(lane.batches[0].start_date, TODAY)

    def test_progress_reduces_remaining_and_shifts_schedule(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 100))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 1.0, None, None))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 60))
        lanes, _, _ = build_plan(self.store, TODAY)
        batch = lanes[0].batches[0]
        self.assertEqual(batch.completed, 60)
        self.assertEqual(batch.remaining, 40)
        self.assertAlmostEqual(batch.pct_complete, 60.0)
        self.assertEqual(batch.finish_date, TODAY)  # 40 remaining at 40/day

    def test_fully_completed_batch_has_no_dates(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 40))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 1.0, None, None))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 40))
        lanes, _, _ = build_plan(self.store, TODAY)
        batch = lanes[0].batches[0]
        self.assertEqual(batch.remaining, 0)
        self.assertIsNone(batch.start_date)
        self.assertIsNone(batch.finish_date)

    def test_missing_allocation_warns_and_blocks_later_batches(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 40))
        self.store.set_requirement(Requirement("Eval", "qc", "B2", 40))
        self.store.set_allocation(Allocation("Eval", "qc", "B2", "op1", 1.0, None, None))
        lanes, _, warnings = build_plan(self.store, TODAY)
        b1, b2 = lanes[0].batches
        self.assertIsNone(b1.start_date)
        self.assertIsNone(b2.start_date)  # blocked behind unscheduled B1
        self.assertTrue(any("no capacity" in w for w in warnings))

    def test_allocation_date_window_limits_capacity(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 80))
        self.store.set_allocation(
            Allocation(
                "Eval",
                "qc",
                "B1",
                "op1",
                1.0,
                date(2025, 1, 10),
                date(2025, 1, 10),
            )
        )
        lanes, _, _ = build_plan(self.store, TODAY)
        # capacity is evaluated on the batch start day (TODAY), outside window
        batch = lanes[0].batches[0]
        self.assertEqual(batch.capacity_per_day, 0.0)

    def test_inactive_operator_contributes_no_capacity(self):
        self.store.upsert_operator(Operator("op1", "Alice", 40.0, False))
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 40))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 1.0, None, None))
        lanes, _, warnings = build_plan(self.store, TODAY)
        self.assertEqual(lanes[0].batches[0].capacity_per_day, 0.0)

    def test_over_allocation_warning(self):
        self.store.set_requirement(Requirement("Eval", "qc", "B1", 40))
        self.store.set_requirement(Requirement("FT", "train", "B1", 40))
        self.store.set_allocation(Allocation("Eval", "qc", "B1", "op1", 1.0, None, None))
        self.store.set_allocation(Allocation("FT", "train", "B1", "op1", 1.0, None, None))
        _, utilization, warnings = build_plan(self.store, TODAY, horizon_days=5)
        self.assertTrue(any(u.over_allocated for u in utilization))
        self.assertTrue(any("over-allocated" in w for w in warnings))

    def test_completion_summary_groups_by_slice(self):
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 30))
        self.store.log_progress(ProgressEntry(TODAY, "Eval", "qc", "B1", 10))
        self.store.log_progress(
            ProgressEntry(date(2025, 1, 7), "Eval", "qc", "B1", 5)
        )
        self.store.log_progress(ProgressEntry(TODAY, "Test", "smoke", "B1", 7))
        rows = completion_summary(self.store)
        self.assertEqual(len(rows), 2)
        qc = next(r for r in rows if r["purpose"] == "qc")
        self.assertEqual(qc["completed"], 45)
        self.assertEqual(qc["first_day"], TODAY.isoformat())
        self.assertEqual(qc["last_day"], "2025-01-07")


if __name__ == "__main__":
    unittest.main()
