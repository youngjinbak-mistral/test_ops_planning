"""Google Sheets sync (sheet -> tool, pull only).

ONE spreadsheet with three tabs, named exactly: team, lanes, progress.
Columns match the local CSVs:

- team:     from_date, to_date, total_hc, throughput_per_day
            (date-ranged rows; empty dates = open-ended; rows must not overlap)
- lanes:    data_type, purpose, batch, total_required, assigned_hc, status
- progress: date, data_type, purpose, batch, items_completed

The sheet must be shared as "Anyone with the link (Viewer)"; the tool reads
its public per-tab CSV export, so no Google account or API key is needed on
the tool side.

Pull semantics:
- team:     replaced (all rows)
- lanes:    replaced (full snapshot of tasks)
- progress: MERGED — items are summed per (date, task), so entries logged
            through the web UI are kept.

Config (the spreadsheet ID) is stored in config.json inside the data dir
('sheet set <ID>'), or can come from the ANNOTATION_SHEET_ID env var.
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .store import (
    Lane,
    ProgressEntry,
    Store,
    StoreError,
    TeamRow,
    _parse_progress_date,
    parse_date,
)

EXPORT_URL = (
    "https://docs.google.com/spreadsheets/d/{sheet_id}"
    "/gviz/tq?tqx=out:csv&sheet={tab}"
)

TABS = ("team", "lanes", "progress")


@dataclass
class SyncResult:
    sheet_id: str
    team_rows: int
    lanes_replaced: int
    progress_added: int
    progress_updated: int


def _read_config(store: Store) -> dict:
    path = store.root / "config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(store: Store, cfg: dict) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "config.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8"
    )


def set_sheet_id(store: Store, sheet_id: str) -> None:
    sheet_id = sheet_id.strip()
    if not sheet_id:
        raise StoreError("sheet id must not be empty")
    cfg = _read_config(store)
    cfg["sheet_id"] = sheet_id
    _write_config(store, cfg)


def get_sheet_id(store: Store) -> str | None:
    return _read_config(store).get("sheet_id") or os.environ.get(
        "ANNOTATION_SHEET_ID"
    )


def _fetch_tab_csv(sheet_id: str, tab: str) -> list[dict[str, str]]:
    url = EXPORT_URL.format(sheet_id=sheet_id, tab=tab)
    req = urllib.request.Request(url, headers={"User-Agent": "annotation-planner"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8-sig")
    except Exception as exc:
        raise StoreError(
            f"could not fetch tab '{tab}' from sheet {sheet_id}: {exc}. "
            "Check that the spreadsheet is shared as 'Anyone with the link "
            "(Viewer)' and has tabs named exactly 'team', 'lanes', 'progress'."
        )
    return list(csv.DictReader(io.StringIO(text)))


def _parse_team(rows: list[dict[str, str]]) -> list[TeamRow]:
    out = []
    for row in rows:
        if not (row.get("total_hc") or "").strip():
            continue
        out.append(
            TeamRow(
                from_date=parse_date(row.get("from_date", ""), "from_date"),
                to_date=parse_date(row.get("to_date", ""), "to_date"),
                total_hc=float(row["total_hc"].strip()),
                throughput_per_day=float(row["throughput_per_day"].strip()),
            )
        )
    return out


def _parse_lanes(rows: list[dict[str, str]]) -> list[Lane]:
    lanes = []
    for row in rows:
        if not (row.get("data_type") or "").strip():
            continue
        lanes.append(
            Lane(
                data_type=row["data_type"].strip(),
                purpose=(row.get("purpose") or "").strip(),
                batch=(row.get("batch") or "").strip(),
                total_required=int((row.get("total_required") or "0").strip() or 0),
                assigned_hc=float((row.get("assigned_hc") or "0").strip() or 0),
                status=(row.get("status") or "not_started").strip(),
            )
        )
    return lanes


def _parse_progress(rows: list[dict[str, str]]) -> list[ProgressEntry]:
    entries = []
    for row in rows:
        if not (row.get("date") or "").strip():
            continue
        d = _parse_progress_date(row["date"])
        entries.append(
            ProgressEntry(
                date=d,
                data_type=row["data_type"].strip(),
                purpose=(row.get("purpose") or "").strip(),
                batch=(row.get("batch") or "").strip(),
                items_completed=int((row.get("items_completed") or "0").strip() or 0),
            )
        )
    return entries


def pull(store: Store, sheet_id: str | None = None) -> SyncResult:
    """Pull team/lanes/progress from the single Google Sheet into the store."""
    sheet_id = (sheet_id or get_sheet_id(store) or "").strip()
    if not sheet_id:
        raise StoreError(
            "no sheet configured; run 'sheet set <SPREADSHEET_ID>' first "
            "(or set the ANNOTATION_SHEET_ID environment variable)"
        )

    team_rows = _parse_team(_fetch_tab_csv(sheet_id, "team"))
    lanes = _parse_lanes(_fetch_tab_csv(sheet_id, "lanes"))
    progress = _parse_progress(_fetch_tab_csv(sheet_id, "progress"))

    store.replace_team_rows(team_rows)
    store.replace_lanes(lanes)
    added, updated = store.merge_progress(progress)

    return SyncResult(
        sheet_id=sheet_id,
        team_rows=len(team_rows),
        lanes_replaced=len(lanes),
        progress_added=added,
        progress_updated=updated,
    )
