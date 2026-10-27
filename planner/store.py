"""CSV-backed storage for the annotation planner.

The directory passed to :class:`Store` holds four CSV files which are the
source of truth and can also be edited directly in a spreadsheet:

- operators.csv     operator_id, name, throughput_per_day, active
- requirements.csv  data_type, purpose, batch, total_required
- allocations.csv   data_type, purpose, batch, operator_id, hc_fraction, from_date, to_date
- progress.csv      date, data_type, purpose, batch, items_completed
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA_TYPES = ("Eval", "FT", "Test")

HEADERS = {
    "operators": ["operator_id", "name", "throughput_per_day", "active"],
    "requirements": ["data_type", "purpose", "batch", "total_required"],
    "allocations": [
        "data_type",
        "purpose",
        "batch",
        "operator_id",
        "hc_fraction",
        "from_date",
        "to_date",
    ],
    "progress": ["date", "data_type", "purpose", "batch", "items_completed"],
}

_BATCH_RE = re.compile(r"^B\d+$")


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class Operator:
    operator_id: str
    name: str
    throughput_per_day: float
    active: bool


@dataclass(frozen=True)
class Requirement:
    data_type: str
    purpose: str
    batch: str
    total_required: int


@dataclass(frozen=True)
class Allocation:
    data_type: str
    purpose: str
    batch: str
    operator_id: str
    hc_fraction: float
    from_date: date | None
    to_date: date | None


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
        raise StoreError(f"invalid data_type {value!r}; expected one of {', '.join(DATA_TYPES)}")
    return value


def validate_batch(value: str) -> str:
    value = value.strip()
    if not _BATCH_RE.match(value):
        raise StoreError(f"invalid batch {value!r}; expected format like B1, B2, ...")
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


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y")


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

    # ----- operators -------------------------------------------------

    def list_operators(self) -> list[Operator]:
        out = []
        for row in self._read("operators"):
            out.append(
                Operator(
                    operator_id=row["operator_id"].strip(),
                    name=row["name"].strip(),
                    throughput_per_day=_parse_float(
                        row["throughput_per_day"], "throughput_per_day"
                    ),
                    active=_parse_bool(row.get("active", "true")),
                )
            )
        return out

    def get_operator(self, operator_id: str) -> Operator:
        for op in self.list_operators():
            if op.operator_id == operator_id:
                return op
        raise StoreError(f"unknown operator_id {operator_id!r}")

    def upsert_operator(self, op: Operator) -> None:
        rows = self._read("operators")
        replaced = False
        for row in rows:
            if row["operator_id"].strip() == op.operator_id:
                row.update(
                    operator_id=op.operator_id,
                    name=op.name,
                    throughput_per_day=str(op.throughput_per_day),
                    active="true" if op.active else "false",
                )
                replaced = True
        if not replaced:
            rows.append(
                {
                    "operator_id": op.operator_id,
                    "name": op.name,
                    "throughput_per_day": str(op.throughput_per_day),
                    "active": "true" if op.active else "false",
                }
            )
        self._write("operators", rows)

    # ----- requirements ----------------------------------------------

    def list_requirements(self) -> list[Requirement]:
        out = []
        for row in self._read("requirements"):
            data_type = validate_data_type(row["data_type"])
            batch = validate_batch(row["batch"])
            out.append(
                Requirement(
                    data_type=data_type,
                    purpose=row["purpose"].strip(),
                    batch=batch,
                    total_required=_parse_int(row["total_required"], "total_required"),
                )
            )
        return out

    def set_requirement(self, req: Requirement) -> None:
        validate_data_type(req.data_type)
        validate_batch(req.batch)
        if req.total_required < 0:
            raise StoreError("total_required must be >= 0")
        if not req.purpose:
            raise StoreError("purpose must not be empty")
        rows = self._read("requirements")
        key = (req.data_type, req.purpose, req.batch)
        replaced = False
        for row in rows:
            if (
                row["data_type"].strip(),
                row["purpose"].strip(),
                row["batch"].strip(),
            ) == key:
                row["total_required"] = str(req.total_required)
                replaced = True
        if not replaced:
            rows.append(
                {
                    "data_type": req.data_type,
                    "purpose": req.purpose,
                    "batch": req.batch,
                    "total_required": str(req.total_required),
                }
            )
        self._write("requirements", rows)

    def delete_requirement(self, data_type: str, purpose: str, batch: str) -> None:
        rows = self._read("requirements")
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
            raise StoreError(f"no requirement for {data_type}/{purpose}/{batch}")
        self._write("requirements", kept)

    # ----- allocations -----------------------------------------------

    def list_allocations(self) -> list[Allocation]:
        out = []
        for row in self._read("allocations"):
            out.append(
                Allocation(
                    data_type=validate_data_type(row["data_type"]),
                    purpose=row["purpose"].strip(),
                    batch=validate_batch(row["batch"]),
                    operator_id=row["operator_id"].strip(),
                    hc_fraction=_parse_float(row["hc_fraction"], "hc_fraction"),
                    from_date=parse_date(row.get("from_date", ""), "from_date"),
                    to_date=parse_date(row.get("to_date", ""), "to_date"),
                )
            )
        return out

    def set_allocation(self, alloc: Allocation) -> None:
        if not 0 < alloc.hc_fraction <= 1:
            raise StoreError("hc_fraction must be in (0, 1]")
        if alloc.from_date and alloc.to_date and alloc.from_date > alloc.to_date:
            raise StoreError("from_date must not be after to_date")
        self.get_operator(alloc.operator_id)
        rows = self._read("allocations")
        key = (alloc.data_type, alloc.purpose, alloc.batch, alloc.operator_id)
        replaced = False
        for row in rows:
            if (
                row["data_type"].strip(),
                row["purpose"].strip(),
                row["batch"].strip(),
                row["operator_id"].strip(),
            ) == key:
                row.update(
                    hc_fraction=str(alloc.hc_fraction),
                    from_date=alloc.from_date.isoformat() if alloc.from_date else "",
                    to_date=alloc.to_date.isoformat() if alloc.to_date else "",
                )
                replaced = True
        if not replaced:
            rows.append(
                {
                    "data_type": alloc.data_type,
                    "purpose": alloc.purpose,
                    "batch": alloc.batch,
                    "operator_id": alloc.operator_id,
                    "hc_fraction": str(alloc.hc_fraction),
                    "from_date": alloc.from_date.isoformat() if alloc.from_date else "",
                    "to_date": alloc.to_date.isoformat() if alloc.to_date else "",
                }
            )
        self._write("allocations", rows)

    def clear_allocation(
        self, data_type: str, purpose: str, batch: str, operator_id: str
    ) -> None:
        rows = self._read("allocations")
        kept = [
            row
            for row in rows
            if not (
                row["data_type"].strip() == data_type
                and row["purpose"].strip() == purpose
                and row["batch"].strip() == batch
                and row["operator_id"].strip() == operator_id
            )
        ]
        if len(kept) == len(rows):
            raise StoreError(
                f"no allocation for {data_type}/{purpose}/{batch}/{operator_id}"
            )
        self._write("allocations", kept)

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
