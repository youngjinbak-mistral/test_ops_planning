"""CSV-backed storage for the annotation planner (team-level model).

The directory passed to :class:`Store` holds CSV files which are the source
of truth and can also be edited directly in a spreadsheet:

- team.csv        total_hc, throughput_per_day
- lanes.csv       data_type, purpose, batch, total_required, assigned_hc, status
- progress.csv    date, data_type, purpose, batch, items_completed
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
    "team": ["total_hc", "throughput_per_day"],
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
class Team:
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

    # ----- team ------------------------------------------------------

    def get_team(self) -> Team:
        rows = self._read("team")
        if not rows:
            return Team(total_hc=0.0, throughput_per_day=0.0)
        row = rows[0]
        return Team(
            total_hc=_parse_float(row["total_hc"], "total_hc"),
            throughput_per_day=_parse_float(
                row["throughput_per_day"], "throughput_per_day"
            ),
        )

    def set_team(self, team: Team) -> None:
        if team.total_hc < 0:
            raise StoreError("total_hc must be >= 0")
        if team.throughput_per_day <= 0:
            raise StoreError("throughput_per_day must be > 0")
        self._write(
            "team",
            [
                {
                    "total_hc": str(team.total_hc),
                    "throughput_per_day": str(team.throughput_per_day),
                }
            ],
        )

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

    # ----- progress ----------------------------------------------------

    def list_progress(self) -> list[ProgressEntry]:
        out = []
        for row in self._read("progress"):
            out.append(
                ProgressEntry(
                    date=parse_date(row["date"]) or date.min,
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
