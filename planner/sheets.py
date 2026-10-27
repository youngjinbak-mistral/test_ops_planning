"""Google Sheets sync (sheet -> tool, pull only).

Two supported sources:

1. One spreadsheet per table ("file-per-tab" mode). The user creates three
   Google Sheets whose titles start with a common prefix, e.g.
   "Annotation Planner Data - team", "... - lanes", "... - progress".
   The tool locates each file by title prefix and downloads its CSV export
   through the Drive API, so no sheet IDs need to be stored in config.

2. Sync semantics:
   - team:     replaced (single row)
   - lanes:    replaced (full snapshot of tasks)
   - progress: MERGED — items are summed per (date, task), so entries logged
               through the web UI are kept.

Config (the title prefix) is stored in config.json inside the data dir.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .store import (
    Lane,
    ProgressEntry,
    Store,
    StoreError,
    Team,
    _parse_progress_date,
)


@dataclass
class SyncResult:
    source: str
    team_synced: bool
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


def set_sheet_prefix(store: Store, prefix: str) -> None:
    prefix = prefix.strip()
    if not prefix:
        raise StoreError("sheet title prefix must not be empty")
    cfg = _read_config(store)
    cfg["sheet_prefix"] = prefix
    _write_config(store, cfg)


def get_sheet_prefix(store: Store) -> str | None:
    path = store.root / "config.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sheet_prefix")
    except (json.JSONDecodeError, OSError):
        return None


def _fetch_csv_by_id(sheet_id: str) -> list[dict[str, str]]:
    """Download one Google Sheets file as CSV rows (first worksheet)."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    req = urllib.request.Request(url, headers={"User-Agent": "annotation-planner"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8-sig")
    except Exception as exc:
        raise StoreError(
            f"could not fetch sheet {sheet_id}: {exc}. "
            "Check that the file is a Google Sheets spreadsheet and is shared "
            "as 'Anyone with the link (Viewer)'."
        )
    return list(csv.DictReader(io.StringIO(text)))


_CONFIG_ROOT: str | None = None


def set_config_root(root: str | Path) -> None:
    global _CONFIG_ROOT
    _CONFIG_ROOT = str(root)


def _find_sheet_id_by_suffix(prefix: str, suffix: str, store: Store | None = None) -> str:
    """Find a spreadsheet whose title contains '<prefix>' and '<suffix>'.

    Resolution order:
    1. config.json entries sheet_ids.{team,lanes,progress} (exact IDs),
    2. environment variables ANNOTATION_SHEET_ID_<SUFFIX>,
    3. Drive search via the Drive API if GOOGLE_DRIVE_TOKEN/GOOGLE_TOKEN is set.
    """
    import os

    root = store.root if store is not None else Path(_CONFIG_ROOT or ".")
    cfg_path = root / "config.json"
    cfg: dict = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
    ids = cfg.get("sheet_ids", {})
    if ids.get(suffix):
        return ids[suffix]

    env_id = os.environ.get(f"ANNOTATION_SHEET_ID_{suffix.upper()}")
    if env_id:
        return env_id

    token = os.environ.get("GOOGLE_DRIVE_TOKEN") or os.environ.get("GOOGLE_TOKEN")
    if token:
        query = (
            "mimeType = 'application/vnd.google-apps.spreadsheet' and "
            f"name contains '{prefix}' and name contains '{suffix}' and trashed = false"
        )
        url = (
            "https://www.googleapis.com/drive/v3/files"
            f"?q={urllib.parse.quote(query)}&pageSize=20&fields=files(id,name,mimeType)"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "annotation-planner",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                files = json.loads(resp.read().decode("utf-8")).get("files", [])
            if files:
                return files[0]["id"]
        except Exception:
            pass

    raise StoreError(
        f"could not find a Google Sheet for '{suffix}' (prefix {prefix!r}). "
        "Set exact IDs with 'sheet set-id team <ID>' / 'sheet set-id lanes <ID>' / "
        f"'sheet set-id progress <ID>', or set ANNOTATION_SHEET_ID_{suffix.upper()}. "
        "The IDs are the long string in each spreadsheet's URL."
    )


def set_sheet_id(store: Store, suffix: str, sheet_id: str) -> None:
    """Store an exact spreadsheet ID for one tab (team/lanes/progress)."""
    suffix = suffix.strip().lower()
    if suffix not in ("team", "lanes", "progress"):
        raise StoreError("suffix must be one of team, lanes, progress")
    sheet_id = sheet_id.strip()
    if not sheet_id:
        raise StoreError("sheet id must not be empty")
    cfg = _read_config(store)
    cfg.setdefault("sheet_ids", {})[suffix] = sheet_id
    _write_config(store, cfg)


def get_sheet_ids(store: Store) -> dict[str, str]:
    path = store.root / "config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sheet_ids", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_team(rows: list[dict[str, str]]) -> Team | None:
    for row in rows:
        if not (row.get("total_hc") or "").strip():
            continue
        return Team(
            total_hc=float(row["total_hc"].strip()),
            throughput_per_day=float(row["throughput_per_day"].strip()),
        )
    return None


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


def pull(store: Store, prefix: str | None = None) -> SyncResult:
    """Pull team/lanes/progress from the user's Google Sheets into the store."""
    prefix = (prefix or get_sheet_prefix(store) or "").strip()
    ids = get_sheet_ids(store)
    if not prefix and not ids:
        raise StoreError(
            "no sheet configured; run 'sheet set <TITLE_PREFIX>' or "
            "'sheet set-id team|lanes|progress <SPREADSHEET_ID>' first"
        )

    team_id = _find_sheet_id_by_suffix(prefix, "team", store)
    lanes_id = _find_sheet_id_by_suffix(prefix, "lanes", store)
    progress_id = _find_sheet_id_by_suffix(prefix, "progress", store)

    team = _parse_team(_fetch_csv_by_id(team_id))
    lanes = _parse_lanes(_fetch_csv_by_id(lanes_id))
    progress = _parse_progress(_fetch_csv_by_id(progress_id))

    if team is not None:
        store.set_team(team)
    store.replace_lanes(lanes)
    added, updated = store.merge_progress(progress)

    return SyncResult(
        source=prefix,
        team_synced=team is not None,
        lanes_replaced=len(lanes),
        progress_added=added,
        progress_updated=updated,
    )
