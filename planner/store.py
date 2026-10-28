"""CSV-backed storage for the annotation planner (team-level model).

The directory passed to :class:`Store` holds CSV files which are the source
of truth and can also be edited directly in a spreadsheet:

- team.csv      from_date, to_date, total_hc, throughput_per_day
                (date-ranged rows; empty dates = open-ended; rows must
                not overlap, so HC and throughput can differ per day)
- lanes.csv     data_type, purpose, batch, total_required, assigned_hc, status
- progress.csv  date, data_type, purpose, batch, items_completed
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA_TYPES = ("Eval", "FT", "Test")
STATUSES = ("not_started", "in_progress", "completed", "stopped")

HEADERS = {
    "team": ["from_date", "to_date", "total_hc", "throughput_per_day"],
    "lanes": [
        "data_type",
        "purpose",
        "batch",
        "total_required",
        "assigned_hc",
        "status",
    ],
    "progress": ["date", "data_type", "purpose", "batch", "items_completed"],
}

_BATCH_RE = re.compile(r"^B\d+$")


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class TeamRow:
    from_date: date | None
    to_date: date | None
    total_hc: float
    throughput_per_day: float


@dataclass(frozen=True)
class Lane:
    data_type: str
    purpose: str
    batch: str
    total_required: int
    assigned_hc: float
    status: str


@dataclass(frozen=True)
class ProgressEntry:
    date: date
    data_type: str
    purpose: str
    batch: str
    items_completed: int


def parse_date(value: str, field: str = "date") -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise StoreError(f"invalid {field}: {value!r} (expected YYYY-MM-DD)")


def _parse_progress_date(value: str) -> date:
    """Parse a progress date. Sheets may export dates as 'YYYY-MM-DD HH:MM:SS'."""
    value = value.strip()
    parsed = parse_date(value)
    if parsed is not None:
        return parsed
    if len(value) > 10:
        parsed = parse_date(value[:10])
        if parsed is not None:
            return parsed
    raise StoreError(f"invalid date: {value!r} (expected YYYY-MM-DD)")


def validate_data_type(value: str) -> str:
    value = value.strip()
    if value not in DATA_TYPES:
        raise StoreError(
            f"invalid data_type {value!r}; expected one of {', '.join(DATA_TYPES)}"
        )
    return value


def validate_batch(value: str) -> str:
    value = value.strip()
    if not _BATCH_RE.match(value):
        raise StoreError(f"invalid batch {value!r}; expected format like B1, B2, ...")
    return value


def validate_status(value: str) -> str:
    value = value.strip()
    if value not in STATUSES:
        raise StoreError(
            f"invalid status {value!r}; expected one of {', '.join(STATUSES)}"
        )
    return value


def _parse_int(value: str, field: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        raise StoreError(f"invalid {field}: {value!r} (expected an integer)")


def _parse_float(value: str, field: str) -> float:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        raise StoreError(f"invalid {field}: {value!r} (expected a number)")


def _validate_team_row(row: TeamRow) -> None:
    if row.total_hc < 0:
        raise StoreError("total_hc must be >= 0")
    if row.throughput_per_day <= 0:
        raise StoreError("throughput_per_day must be > 0")
    if row.from_date and row.to_date and row.from_date > row.to_date:
        raise StoreError("from_date must not be after to_date")


def _ranges_overlap(a: TeamRow, b: TeamRow) -> bool:
    """True if the two date ranges share at least one calendar day.

    An open-ended side extends only up to the other row's boundary: a row
    (…, 2025-01-31) and a row (2025-02-01, …) do NOT overlap, while a row
    (…, None) and a row (2025-02-01, …) DO (the open-ended row covers
    2025-02-01 onwards). Two rows both open on the same side overlap.
    """
    a_start = a.from_date or date.min
    a_end = a.to_date or date.max
    b_start = b.from_date or date.min
    b_end = b.to_date or date.max
    if a_start > b_end or b_start > a_end:
        return False
    # If both rows are bounded on the same side, the interval check above
    # already decided: no shared day. Only open-ended sides need extra care,
    # because date.min/date.max placeholders can mask a real gap.
    if a.from_date is None and a.to_date is None:
        return True  # fully open row covers everything
    if b.from_date is None and b.to_date is None:
        return True
    if a.from_date is None and b.from_date is None:
        return True  # both extend left; ranges share days
    if a.to_date is None and b.to_date is None:
        return True  # both extend right; ranges share days
    if a.from_date is None and b.to_date is None:
        # a ends at a_end, b starts at b_start; overlap iff a reaches into b
        return a_end >= b_start
    if b.from_date is None and a.to_date is None:
        return b_end >= a_start
    if a.from_date is None:
        # a is (..., a_end]; b is fully bounded -> overlap iff a reaches b
        return a_end >= b_start
    if b.from_date is None:
        return b_end >= a_start
    if a.to_date is None:
        # a is [a_start, ...); b is fully bounded
        return a_start <= b_end
    if b.to_date is None:
        return b_start <= a_end
    return True


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in HEADERS:
            path = self.root / f"{name}.csv"
            if not path.exists():
                self._write(name, [])

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.csv"

    def _read(self, name: str) -> list[dict[str, str]]:
        path = self._path(name)
        if not path.exists():
            raise StoreError(
                f"{path} not found; run 'init' first or pass the correct --dir"
            )
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def _write(self, name: str, rows: list[dict[str, str]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._path(name).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=HEADERS[name])
            writer.writeheader()
            writer.writerows(rows)

    # ----- team (date-ranged HC & throughput) ------------------------

    def list_team_rows(self) -> list[TeamRow]:
        out = []
        for row in self._read("team"):
            out.append(
                TeamRow(
                    from_date=parse_date(row.get("from_date", ""), "from_date"),
                    to_date=parse_date(row.get("to_date", ""), "to_date"),
                    total_hc=_parse_float(row["total_hc"], "total_hc"),
                    throughput_per_day=_parse_float(
                        row["throughput_per_day"], "throughput_per_day"
                    ),
                )
            )
        return out

    def team_on(self, day: date) -> TeamRow | None:
        for row in self.list_team_rows():
            if (row.from_date is None or day >= row.from_date) and (
                row.to_date is None or day <= row.to_date
            ):
                return row
        return None

    def add_team_row(self, row: TeamRow) -> None:
        _validate_team_row(row)
        rows = self.list_team_rows()
        for existing in rows:
            if _ranges_overlap(row, existing):
                raise StoreError(
                    "team row overlaps an existing row "
                    f"({existing.from_date or '...'}..{existing.to_date or '...'})"
                )
        rows.append(row)
        self.replace_team_rows(rows)

    def replace_team_rows(self, rows: list[TeamRow]) -> None:
        for row in rows:
            _validate_team_row(row)
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                if _ranges_overlap(a, b):
                    raise StoreError(
                        "team rows overlap: "
                        f"({a.from_date or '...'}..{a.to_date or '...'}) and "
                        f"({b.from_date or '...'}..{b.to_date or '...'})"
                    )
        self._write(
            "team",
            [
                {
                    "from_date": r.from_date.isoformat() if r.from_date else "",
                    "to_date": r.to_date.isoformat() if r.to_date else "",
                    "total_hc": str(r.total_hc),
                    "throughput_per_day": str(r.throughput_per_day),
                }
                for r in rows
            ],
        )

    def clear_team_rows(self) -> None:
        self._write("team", [])

    # ----- lanes -----------------------------------------------------

    def list_lanes(self) -> list[Lane]:
        out = []
        for row in self._read("lanes"):
            out.append(
                Lane(
                    data_type=validate_data_type(row["data_type"]),
                    purpose=row["purpose"].strip(),
                    batch=validate_batch(row["batch"]),
                    total_required=_parse_int(
                        row["total_required"], "total_required"
                    ),
                    assigned_hc=_parse_float(row["assigned_hc"], "assigned_hc"),
                    status=validate_status(row["status"]),
                )
            )
        return out

    def get_lane(self, data_type: str, purpose: str, batch: str) -> Lane:
        for lane in self.list_lanes():
            if (
                lane.data_type == data_type
                and lane.purpose == purpose
                and lane.batch == batch
            ):
                return lane
        raise StoreError(f"no lane for {data_type}/{purpose}/{batch}")

    def set_lane(self, lane: Lane) -> None:
        validate_data_type(lane.data_type)
        validate_batch(lane.batch)
        validate_status(lane.status)
        if not lane.purpose:
            raise StoreError("purpose must not be empty")
        if lane.total_required < 0:
            raise StoreError("total_required must be >= 0")
        if lane.assigned_hc < 0:
            raise StoreError("assigned_hc must be >= 0")
        rows = self._read("lanes")
        key = (lane.data_type, lane.purpose, lane.batch)
        replaced = False
        for row in rows:
            if (
                row["data_type"].strip(),
                row["purpose"].strip(),
                row["batch"].strip(),
            ) == key:
                row.update(
                    total_required=str(lane.total_required),
                    assigned_hc=str(lane.assigned_hc),
                    status=lane.status,
                )
                replaced = True
        if not replaced:
            rows.append(
                {
                    "data_type": lane.data_type,
                    "purpose": lane.purpose,
                    "batch": lane.batch,
                    "total_required": str(lane.total_required),
                    "assigned_hc": str(lane.assigned_hc),
                    "status": lane.status,
                }
            )
        self._write("lanes", rows)

    def set_lane_status(self, data_type: str, purpose: str, batch: str, status: str) -> None:
        lane = self.get_lane(data_type, purpose, batch)
        self.set_lane(
            Lane(
                lane.data_type,
                lane.purpose,
                lane.batch,
                lane.total_required,
                lane.assigned_hc,
                validate_status(status),
            )
        )

    def delete_lane(self, data_type: str, purpose: str, batch: str) -> None:
        rows = self._read("lanes")
        kept = [
            row
            for row in rows
            if not (
                row["data_type"].strip() == data_type
                and row["purpose"].strip() == purpose
                and row["batch"].strip() == batch
            )
        ]
        if len(kept) == len(rows):
            raise StoreError(f"no lane for {data_type}/{purpose}/{batch}")
        self._write("lanes", kept)

    def replace_lanes(self, lanes: list[Lane]) -> None:
        """Replace all lanes (used by sheet sync)."""
        for lane in lanes:
            validate_data_type(lane.data_type)
            validate_batch(lane.batch)
            validate_status(lane.status)
            if not lane.purpose:
                raise StoreError("purpose must not be empty")
            if lane.total_required < 0:
                raise StoreError("total_required must be >= 0")
            if lane.assigned_hc < 0:
                raise StoreError("assigned_hc must be >= 0")
        seen: set[tuple[str, str, str]] = set()
        for lane in lanes:
            key = (lane.data_type, lane.purpose, lane.batch)
            if key in seen:
                raise StoreError(
                    f"duplicate lane {lane.data_type}/{lane.purpose}/{lane.batch}"
                )
            seen.add(key)
        self._write(
            "lanes",
            [
                {
                    "data_type": l.data_type,
                    "purpose": l.purpose,
                    "batch": l.batch,
                    "total_required": str(l.total_required),
                    "assigned_hc": str(l.assigned_hc),
                    "status": l.status,
                }
                for l in lanes
            ],
        )

    # ----- progress ----------------------------------------------------

    def list_progress(self) -> list[ProgressEntry]:
        out = []
        for row in self._read("progress"):
            out.append(
                ProgressEntry(
                    date=_parse_progress_date(row["date"]),
                    data_type=validate_data_type(row["data_type"]),
                    purpose=row["purpose"].strip(),
                    batch=validate_batch(row["batch"]),
                    items_completed=_parse_int(
                        row["items_completed"], "items_completed"
                    ),
                )
            )
        return out

    def log_progress(self, entry: ProgressEntry) -> None:
        if entry.items_completed <= 0:
            raise StoreError("items_completed must be > 0")
        rows = self._read("progress")
        rows.append(
            {
                "date": entry.date.isoformat(),
                "data_type": entry.data_type,
                "purpose": entry.purpose,
                "batch": entry.batch,
                "items_completed": str(entry.items_completed),
            }
        )
        self._write("progress", rows)

    def merge_progress(self, entries: list[ProgressEntry]) -> tuple[int, int]:
        """Merge external progress entries; sums items per (date, task).

        Used by sheet sync: entries already present locally (same date and
        task) are summed in place, new ones appended. Returns
        (new_entries_added, existing_entries_updated).
        """
        existing: dict[tuple[date, str, str, str], int] = {}
        rows = self._read("progress")
        for row in rows:
            d = _parse_progress_date(row["date"])
            key = (
                d,
                row["data_type"].strip(),
                row["purpose"].strip(),
                row["batch"].strip(),
            )
            existing[key] = existing.get(key, 0) + _parse_int(
                row["items_completed"], "items_completed"
            )
        added = 0
        updated = 0
        for e in entries:
            if e.items_completed <= 0:
                raise StoreError("items_completed must be > 0")
            key = (e.date, e.data_type, e.purpose, e.batch)
            if key in existing:
                existing[key] += e.items_completed
                updated += 1
                for row in rows:
                    rd = _parse_progress_date(row["date"])
                    rkey = (
                        rd,
                        row["data_type"].strip(),
                        row["purpose"].strip(),
                        row["batch"].strip(),
                    )
                    if rkey == key:
                        row["items_completed"] = str(existing[key])
                        break
            else:
                existing[key] = e.items_completed
                rows.append(
                    {
                        "date": e.date.isoformat(),
                        "data_type": e.data_type,
                        "purpose": e.purpose,
                        "batch": e.batch,
                        "items_completed": str(e.items_completed),
                    }
                )
                added += 1
        self._write("progress", rows)
        return added, updated
