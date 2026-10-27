"""Planning engine: derived metrics over the CSV store (team-level model).

Model:
- The team has a total HC and one throughput (items per HC per day).
- Each lane (data_type, purpose, batch) has: total_required, assigned_hc,
  and a status (not_started / in_progress / completed / stopped).
- Capacity per day for a lane = assigned_hc * throughput_per_day.
- Lanes with status 'stopped' or 'completed' are not scheduled; other lanes
  are scheduled sequentially per (data_type, purpose) group, in batch order,
  starting at the planning date.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .store import Lane, ProgressEntry, Store, Team

WORKDAY = timedelta(days=1)


@dataclass(frozen=True)
class LaneKey:
    data_type: str
    purpose: str
    batch: str


@dataclass
class LanePlan:
    lane: Lane
    completed: int
    capacity_per_day: float
    start_date: date | None
    finish_date: date | None

    @property
    def remaining(self) -> int:
        return max(self.lane.total_required - self.completed, 0)

    @property
    def pct_complete(self) -> float:
        if self.lane.total_required == 0:
            return 100.0
        return 100.0 * self.completed / self.lane.total_required


@dataclass
class Snapshot:
    total_required: int
    total_completed: int
    total_in_progress: int

    @property
    def pct_complete(self) -> float:
        if self.total_required == 0:
            return 100.0
        return 100.0 * self.total_completed / self.total_required


@dataclass
class PlanReport:
    team: Team
    lanes: list[LanePlan]
    snapshot: Snapshot
    daily: dict[date, dict[LaneKey, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _completed_by_key(progress: list[ProgressEntry]) -> dict[LaneKey, int]:
    totals: dict[LaneKey, int] = defaultdict(int)
    for p in progress:
        totals[LaneKey(p.data_type, p.purpose, p.batch)] += p.items_completed
    return dict(totals)


def _daily_by_key(
    progress: list[ProgressEntry],
) -> dict[date, dict[LaneKey, int]]:
    daily: dict[date, dict[LaneKey, int]] = defaultdict(lambda: defaultdict(int))
    for p in progress:
        key = LaneKey(p.data_type, p.purpose, p.batch)
        daily[p.date][key] += p.items_completed
    return {d: dict(v) for d, v in daily.items()}


def _batch_number(batch: str) -> int:
    return int(batch[1:])


def build_plan(store: Store, today: date, horizon_days: int = 90) -> PlanReport:
    team = store.get_team()
    lanes = store.list_lanes()
    progress = store.list_progress()

    completed = _completed_by_key(progress)
    daily = _daily_by_key(progress)
    warnings: list[str] = []

    assigned_total = sum(lane.assigned_hc for lane in lanes)
    if team.total_hc > 0 and assigned_total > team.total_hc + 1e-9:
        warnings.append(
            f"assigned HC ({assigned_total:g}) exceeds total HC ({team.total_hc:g})"
        )

    lane_plans: list[LanePlan] = []
    total_required = 0
    total_completed = 0
    total_in_progress = 0

    groups: dict[tuple[str, str], list[Lane]] = defaultdict(list)
    for lane in lanes:
        groups[(lane.data_type, lane.purpose)].append(lane)

    for (data_type, purpose), group in sorted(groups.items()):
        group = sorted(group, key=lambda l: _batch_number(l.batch))
        cursor = today
        blocked = False
        for lane in group:
            key = LaneKey(lane.data_type, lane.purpose, lane.batch)
            done = completed.get(key, 0)
            capacity = lane.assigned_hc * team.throughput_per_day
            remaining = max(lane.total_required - done, 0)

            total_required += lane.total_required
            total_completed += min(done, lane.total_required)
            if lane.status == "in_progress":
                total_in_progress += remaining

            if lane.status in ("completed", "stopped") or remaining <= 0:
                lane_plans.append(
                    LanePlan(lane, done, capacity, None, None)
                )
                continue

            if blocked or capacity <= 0:
                lane_plans.append(LanePlan(lane, done, capacity, None, None))
                if not blocked:
                    blocked = True
                    warnings.append(
                        f"{lane.data_type}/{lane.purpose}/{lane.batch}: "
                        "no HC assigned; not scheduled"
                    )
                continue

            days = -(-remaining // capacity)  # ceil
            finish = cursor + timedelta(days=int(days) - 1)
            lane_plans.append(
                LanePlan(lane, done, capacity, cursor, finish)
            )
            cursor = finish + WORKDAY

    snapshot = Snapshot(total_required, total_completed, total_in_progress)
    return PlanReport(
        team=team,
        lanes=lane_plans,
        snapshot=snapshot,
        daily=daily,
        warnings=warnings,
    )


def plan_dates(report: PlanReport) -> list[date]:
    """All dates that should appear as columns in the daily grid.

    Union of logged progress dates and scheduled start..finish ranges,
    limited to the horizon from the earliest scheduled date.
    """
    dates: set[date] = set(report.daily.keys())
    starts = [lp.start_date for lp in report.lanes if lp.start_date]
    if starts:
        first = min(starts)
        last = max(
            (lp.finish_date for lp in report.lanes if lp.finish_date),
            default=first,
        )
        day = first
        while day <= last:
            dates.add(day)
            day += WORKDAY
    return sorted(dates)
