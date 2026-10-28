"""Planning engine: derived metrics over the CSV store (team-level model).

Model:
- Team rows are date-ranged: each row gives total HC and throughput
  (items per HC per day) for its [from_date, to_date] window. Days without
  a matching row have zero capacity. Rows must not overlap.
- Each lane (data_type, purpose, batch) has: total_required, assigned_hc,
  and a status (not_started / in_progress / completed / stopped).
- Capacity per day for a lane = assigned_hc * throughput_per_day(on that day).
- Lanes with status 'stopped' or 'completed' are not scheduled; other lanes
  are scheduled sequentially per (data_type, purpose) group, in batch order,
  starting at the planning date. The scheduler walks day by day, consuming
  that day's capacity until the batch's remaining work is done.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .store import Lane, ProgressEntry, Store, TeamRow

WORKDAY = timedelta(days=1)
MAX_SCHEDULE_DAYS = 3650  # safety cap for the day-walking scheduler


@dataclass(frozen=True)
class LaneKey:
    data_type: str
    purpose: str
    batch: str


@dataclass
class LanePlan:
    lane: Lane
    completed: int
    start_date: date | None
    finish_date: date | None
    capacity_by_day: dict[date, float] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return max(self.lane.total_required - self.completed, 0)

    @property
    def pct_complete(self) -> float:
        if self.lane.total_required == 0:
            return 100.0
        return 100.0 * self.completed / self.lane.total_required

    @property
    def capacity_per_day(self) -> float:
        """Representative daily capacity (capacity on the start day)."""
        if self.start_date is None:
            return 0.0
        return self.capacity_by_day.get(self.start_date, 0.0)


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
class TeamSummary:
    """Aggregated view of the date-ranged team rows."""

    rows: list[TeamRow]
    peak_hc: float
    peak_throughput: float


@dataclass
class PlanReport:
    team: TeamSummary
    lanes: list[LanePlan]
    snapshot: Snapshot
    daily: dict[date, dict[LaneKey, int]] = field(default_factory=dict)
    team_daily: dict[date, dict[str, float]] = field(default_factory=dict)
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
    team_rows = store.list_team_rows()
    lanes = store.list_lanes()
    progress = store.list_progress()

    completed = _completed_by_key(progress)
    daily = _daily_by_key(progress)
    warnings: list[str] = []

    if not team_rows:
        warnings.append(
            "no team rows configured; add at least one row with total HC and "
            "throughput (Team page or the 'team' tab)"
        )

    assigned_total = sum(lane.assigned_hc for lane in lanes)
    peak_hc = max((r.total_hc for r in team_rows), default=0.0)
    if peak_hc > 0 and assigned_total > peak_hc + 1e-9:
        warnings.append(
            f"assigned HC ({assigned_total:g}) exceeds peak total HC ({peak_hc:g})"
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
            remaining = max(lane.total_required - done, 0)

            total_required += lane.total_required
            total_completed += min(done, lane.total_required)
            if lane.status == "in_progress":
                total_in_progress += remaining

            if lane.status in ("completed", "stopped") or remaining <= 0:
                lane_plans.append(LanePlan(lane, done, None, None))
                continue

            if blocked:
                lane_plans.append(LanePlan(lane, done, None, None))
                continue

            # Day-walking schedule: consume each day's capacity until done.
            capacity_by_day: dict[date, float] = {}
            day = cursor
            left = float(remaining)
            scheduled = False
            for _ in range(MAX_SCHEDULE_DAYS):
                row = store.team_on(day)
                capacity = (
                    lane.assigned_hc * row.throughput_per_day if row else 0.0
                )
                capacity_by_day[day] = capacity
                if capacity <= 0:
                    day += WORKDAY
                    continue
                take = min(left, capacity)
                left -= take
                if not scheduled:
                    scheduled = True
                    start = day
                if left <= 1e-9:
                    lane_plans.append(
                        LanePlan(lane, done, start, day, capacity_by_day)
                    )
                    break
                day += WORKDAY
            else:
                warnings.append(
                    f"{lane.data_type}/{lane.purpose}/{lane.batch}: could not "
                    f"finish within {MAX_SCHEDULE_DAYS} days; check team rows"
                )
                lane_plans.append(LanePlan(lane, done, None, None, capacity_by_day))
                blocked = True
                continue

            if not scheduled:
                lane_plans.append(LanePlan(lane, done, None, None, capacity_by_day))
                blocked = True
                warnings.append(
                    f"{lane.data_type}/{lane.purpose}/{lane.batch}: "
                    "no HC or no team row covers the schedule window; not scheduled"
                )
                continue

            cursor = lane_plans[-1].finish_date + WORKDAY  # type: ignore[operator]

    # per-day team view for the horizon (HC and throughput actually available)
    team_daily: dict[date, dict[str, float]] = {}
    for i in range(horizon_days):
        day = today + timedelta(days=i)
        row = store.team_on(day)
        team_daily[day] = {
            "total_hc": row.total_hc if row else 0.0,
            "throughput": row.throughput_per_day if row else 0.0,
        }

    snapshot = Snapshot(total_required, total_completed, total_in_progress)
    return PlanReport(
        team=TeamSummary(
            rows=team_rows,
            peak_hc=peak_hc,
            peak_throughput=max((r.throughput_per_day for r in team_rows), default=0.0),
        ),
        lanes=lane_plans,
        snapshot=snapshot,
        daily=daily,
        team_daily=team_daily,
        warnings=warnings,
    )


def plan_dates(report: PlanReport) -> list[date]:
    """All dates that should appear as columns in the daily grid.

    Union of logged progress dates and scheduled start..finish ranges.
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
