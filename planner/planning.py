"""Planning engine: derived metrics over the CSV store.

Model:
- Lanes are (data_type, purpose) pairs; different data types run in parallel.
- Within a lane, batches run sequentially: B(n) starts the day after B(n-1)
  is scheduled to finish.
- Capacity for an allocation = hc_fraction * operator throughput_per_day,
  active only within [from_date, to_date] (open-ended when unset).
- A batch with no allocations is unscheduled (start/finish = None).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .store import Allocation, Operator, ProgressEntry, Requirement, Store

WORKDAY = timedelta(days=1)


@dataclass(frozen=True)
class SliceKey:
    data_type: str
    purpose: str
    batch: str


@dataclass
class BatchPlan:
    key: SliceKey
    total_required: int
    completed: int
    capacity_per_day: float
    start_date: date | None
    finish_date: date | None

    @property
    def remaining(self) -> int:
        return self.total_required - self.completed

    @property
    def pct_complete(self) -> float:
        if self.total_required == 0:
            return 100.0
        return 100.0 * self.completed / self.total_required


@dataclass
class LanePlan:
    data_type: str
    purpose: str
    batches: list[BatchPlan]
    start_date: date | None
    finish_date: date | None


@dataclass
class HcUtilization:
    date: date
    total_hc: float
    allocated_hc: float
    operator_over_allocated: bool = False

    @property
    def over_allocated(self) -> bool:
        return (
            self.allocated_hc > self.total_hc + 1e-9
            or self.operator_over_allocated
        )


def _capacity_per_day(
    allocations: list[Allocation], operators: dict[str, Operator], on: date
) -> float:
    total = 0.0
    for alloc in allocations:
        if alloc.from_date and on < alloc.from_date:
            continue
        if alloc.to_date and on > alloc.to_date:
            continue
        op = operators.get(alloc.operator_id)
        if op and op.active:
            total += alloc.hc_fraction * op.throughput_per_day
    return total


def _required_by_key(reqs: list[Requirement]) -> dict[SliceKey, int]:
    return {
        SliceKey(r.data_type, r.purpose, r.batch): r.total_required for r in reqs
    }


def _completed_by_key(progress: list[ProgressEntry]) -> dict[SliceKey, int]:
    totals: dict[SliceKey, int] = defaultdict(int)
    for p in progress:
        totals[SliceKey(p.data_type, p.purpose, p.batch)] += p.items_completed
    return dict(totals)


def _allocations_by_key(
    allocations: list[Allocation],
) -> dict[SliceKey, list[Allocation]]:
    grouped: dict[SliceKey, list[Allocation]] = defaultdict(list)
    for a in allocations:
        grouped[SliceKey(a.data_type, a.purpose, a.batch)].append(a)
    return dict(grouped)


def _batch_number(batch: str) -> int:
    return int(batch[1:])


def build_plan(
    store: Store, today: date, horizon_days: int = 365
) -> tuple[list[LanePlan], list[HcUtilization], list[str]]:
    """Compute the full plan plus HC utilization warnings.

    Returns (lanes, utilization, warnings). Batches within a lane are
    scheduled sequentially starting at `today`; a batch with zero capacity
    is unscheduled and blocks later batches in the same lane.
    """
    operators = {op.operator_id: op for op in store.list_operators()}
    reqs = store.list_requirements()
    allocations = store.list_allocations()
    progress = store.list_progress()

    required = _required_by_key(reqs)
    completed = _completed_by_key(progress)
    allocs = _allocations_by_key(allocations)

    lanes: dict[tuple[str, str], list[BatchPlan]] = defaultdict(list)
    warnings: list[str] = []

    for data_type, purpose in sorted({(r.data_type, r.purpose) for r in reqs}):
        batches = sorted(
            (
                key.batch
                for key in required
                if key.data_type == data_type and key.purpose == purpose
            ),
            key=_batch_number,
        )
        plans: list[BatchPlan] = []
        cursor = today
        blocked = False
        for batch in batches:
            key = SliceKey(data_type, purpose, batch)
            capacity = _capacity_per_day(allocs.get(key, []), operators, cursor)
            done = completed.get(key, 0)
            total = total_of(required, key)
            remaining = max(total - done, 0)
            if remaining <= 0:
                plans.append(
                    BatchPlan(
                        key=key,
                        total_required=total,
                        completed=done,
                        capacity_per_day=capacity,
                        start_date=None,
                        finish_date=None,
                    )
                )
                continue
            if blocked or capacity <= 0:
                plans.append(
                    BatchPlan(
                        key=key,
                        total_required=total,
                        completed=done,
                        capacity_per_day=capacity,
                        start_date=None,
                        finish_date=None,
                    )
                )
                blocked = True
                warnings.append(
                    f"{data_type}/{purpose}/{batch}: no capacity allocated; "
                    "batch is unscheduled"
                )
                continue
            days = -(-remaining // capacity)  # ceil
            finish = cursor + timedelta(days=int(days) - 1)
            plans.append(
                BatchPlan(
                    key=key,
                    total_required=total,
                    completed=done,
                    capacity_per_day=capacity,
                    start_date=cursor,
                    finish_date=finish,
                )
            )
            cursor = finish + WORKDAY
        lanes[(data_type, purpose)] = plans

    lane_plans = [
        LanePlan(
            data_type=dt,
            purpose=pu,
            batches=plans,
            start_date=plans[0].start_date if plans else None,
            finish_date=plans[-1].finish_date if plans else None,
        )
        for (dt, pu), plans in sorted(lanes.items())
    ]

    utilization = _hc_utilization(operators, allocations, today, horizon_days, warnings)
    return lane_plans, utilization, warnings


def total_of(required: dict[SliceKey, int], key: SliceKey) -> int:
    return required.get(key, 0)


def _hc_utilization(
    operators: dict[str, Operator],
    allocations: list[Allocation],
    today: date,
    horizon_days: int,
    warnings: list[str],
) -> list[HcUtilization]:
    active_ops = [op for op in operators.values() if op.active]
    total_hc = sum(1.0 for _ in active_ops)
    if total_hc == 0:
        return []
    by_operator: dict[str, list[Allocation]] = defaultdict(list)
    for a in allocations:
        by_operator[a.operator_id].append(a)
    result = []
    warned_ops: set[str] = set()
    for i in range(horizon_days):
        on = today + timedelta(days=i)
        allocated = 0.0
        any_op_over = False
        for op_id, allocs in by_operator.items():
            frac = sum(
                a.hc_fraction
                for a in allocs
                if (not a.from_date or on >= a.from_date)
                and (not a.to_date or on <= a.to_date)
            )
            allocated += min(frac, 1.0)
            if frac > 1.0 + 1e-9:
                any_op_over = True
                if op_id not in warned_ops:
                    warned_ops.add(op_id)
                    warnings.append(
                        f"operator {op_id} over-allocated ({frac:.2f} HC) starting {on.isoformat()}"
                    )
        result.append(
            HcUtilization(
                date=on,
                total_hc=total_hc,
                allocated_hc=allocated,
                operator_over_allocated=any_op_over,
            )
        )
    over_days = [u for u in result if u.allocated_hc > u.total_hc + 1e-9]
    if over_days:
        warnings.append(
            f"team HC over-allocated on {len(over_days)} day(s), "
            f"first on {over_days[0].date.isoformat()} "
            f"({over_days[0].allocated_hc:.2f} allocated vs {over_days[0].total_hc:.2f} HC)"
        )
    return result


def completion_summary(
    store: Store,
) -> list[dict[str, object]]:
    """Feature 5: completed items grouped by type/purpose/batch and by day."""
    progress = store.list_progress()
    by_slice: dict[SliceKey, int] = defaultdict(int)
    by_day: dict[tuple[date, SliceKey], int] = defaultdict(int)
    for p in progress:
        key = SliceKey(p.data_type, p.purpose, p.batch)
        by_slice[key] += p.items_completed
        by_day[(p.date, key)] += p.items_completed
    rows = []
    for key in sorted(by_slice, key=lambda k: (k.data_type, k.purpose, k.batch)):
        days = sorted(d for (d, k) in by_day if k == key)
        rows.append(
            {
                "data_type": key.data_type,
                "purpose": key.purpose,
                "batch": key.batch,
                "completed": by_slice[key],
                "first_day": days[0].isoformat() if days else "",
                "last_day": days[-1].isoformat() if days else "",
            }
        )
    return rows
