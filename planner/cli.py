"""Command-line interface for the annotation planner.

Usage:
    python3 -m planner.cli --dir data <command> [args...]

Commands:
    init                          create the CSV files in --dir
    web [--host H] [--port P]     launch the web UI (default 127.0.0.1:8000)
    team add [FROM] [TO] HC THROUGHPUT   add a date-ranged team row (dates optional)
    team list
    team clear
    sheet set SHEET_ID            link the Google Sheet (one spreadsheet, tabs: team/lanes/progress)
    sheet show
    sync [--sheet ID]             pull data from the linked Google Sheet
    lane add TYPE PURPOSE BATCH REQUIRED ASSIGNED_HC [STATUS]
    lane list
    lane set TYPE PURPOSE BATCH REQUIRED ASSIGNED_HC STATUS
    lane status TYPE PURPOSE BATCH STATUS   (not_started|in_progress|completed|stopped)
    lane delete TYPE PURPOSE BATCH
    progress log DATE TYPE PURPOSE BATCH ITEMS
    progress summary
    plan [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from .planning import build_plan
from .store import (
    DATA_TYPES,
    STATUSES,
    Lane,
    ProgressEntry,
    Store,
    StoreError,
    TeamRow,
    parse_date,
    validate_batch,
    validate_data_type,
    validate_status,
)


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else "..."


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*row))


def cmd_init(store: Store, args: argparse.Namespace) -> int:
    store.init()
    print(f"initialized planner data in {store.root.resolve()}")
    return 0


def cmd_web(store: Store, args: argparse.Namespace) -> int:
    from .web import run

    run(store, host=args.host, port=args.port)
    return 0


def cmd_team_add(store: Store, args: argparse.Namespace) -> int:
    from_date = parse_date(args.from_date, "from_date") if args.from_date else None
    to_date = parse_date(args.to_date, "to_date") if args.to_date else None
    store.add_team_row(
        TeamRow(
            from_date=from_date,
            to_date=to_date,
            total_hc=float(args.total_hc),
            throughput_per_day=float(args.throughput),
        )
    )
    print(
        f"team row added: {_fmt_date(from_date)}..{_fmt_date(to_date)} "
        f"at {float(args.total_hc):g} HC, {float(args.throughput):g} items/HC/day"
    )
    return 0


def cmd_team_list(store: Store, args: argparse.Namespace) -> int:
    rows = store.list_team_rows()
    _print_table(
        ["from_date", "to_date", "total_hc", "throughput/day"],
        [
            [
                _fmt_date(r.from_date),
                _fmt_date(r.to_date),
                f"{r.total_hc:g}",
                f"{r.throughput_per_day:g}",
            ]
            for r in rows
        ],
    )
    if not rows:
        print("(no team rows; add one with 'team add')")
    return 0


def cmd_team_clear(store: Store, args: argparse.Namespace) -> int:
    store.clear_team_rows()
    print("team rows cleared")
    return 0


def cmd_sheet_set(store: Store, args: argparse.Namespace) -> int:
    from .sheets import set_sheet_id

    set_sheet_id(store, args.sheet_id)
    print(f"Google Sheet linked: {args.sheet_id}")
    print("Expected tabs: team, lanes, progress (share as 'Anyone with the link, Viewer')")
    return 0


def cmd_sheet_show(store: Store, args: argparse.Namespace) -> int:
    from .sheets import get_sheet_id

    print(f"sheet id: {get_sheet_id(store) or '(not configured)'}")
    return 0


def cmd_sync(store: Store, args: argparse.Namespace) -> int:
    from .sheets import pull

    result = pull(store, args.sheet_id)
    print(f"synced from sheet {result.sheet_id}:")
    print(f"  team: {result.team_rows} row(s) replaced")
    print(f"  lanes: {result.lanes_replaced} replaced")
    print(
        f"  progress: {result.progress_added} new, "
        f"{result.progress_updated} merged into existing day(s)"
    )
    return 0


def cmd_lane_add(store: Store, args: argparse.Namespace) -> int:
    store.set_lane(
        Lane(
            data_type=validate_data_type(args.data_type),
            purpose=args.purpose,
            batch=validate_batch(args.batch),
            total_required=int(args.required),
            assigned_hc=float(args.assigned_hc),
            status=validate_status(args.status),
        )
    )
    print(f"lane added: {args.data_type}/{args.purpose}/{args.batch}")
    return 0


def cmd_lane_list(store: Store, args: argparse.Namespace) -> int:
    lanes = store.list_lanes()
    _print_table(
        ["data_type", "purpose", "batch", "required", "assigned_hc", "status"],
        [
            [
                l.data_type,
                l.purpose,
                l.batch,
                str(l.total_required),
                f"{l.assigned_hc:g}",
                l.status,
            ]
            for l in sorted(lanes, key=lambda l: (l.data_type, l.purpose, l.batch))
        ],
    )
    return 0


def cmd_lane_set(store: Store, args: argparse.Namespace) -> int:
    store.set_lane(
        Lane(
            data_type=validate_data_type(args.data_type),
            purpose=args.purpose,
            batch=validate_batch(args.batch),
            total_required=int(args.required),
            assigned_hc=float(args.assigned_hc),
            status=validate_status(args.status),
        )
    )
    print(f"lane updated: {args.data_type}/{args.purpose}/{args.batch}")
    return 0


def cmd_lane_status(store: Store, args: argparse.Namespace) -> int:
    store.set_lane_status(
        validate_data_type(args.data_type),
        args.purpose,
        validate_batch(args.batch),
        validate_status(args.status),
    )
    print(f"status set: {args.data_type}/{args.purpose}/{args.batch} = {args.status}")
    return 0


def cmd_lane_delete(store: Store, args: argparse.Namespace) -> int:
    store.delete_lane(
        validate_data_type(args.data_type), args.purpose, validate_batch(args.batch)
    )
    print("lane deleted")
    return 0


def cmd_progress_log(store: Store, args: argparse.Namespace) -> int:
    entry_date = parse_date(args.date)
    if entry_date is None:
        raise StoreError("date is required (YYYY-MM-DD)")
    store.log_progress(
        ProgressEntry(
            date=entry_date,
            data_type=validate_data_type(args.data_type),
            purpose=args.purpose,
            batch=validate_batch(args.batch),
            items_completed=int(args.items),
        )
    )
    print(f"logged {args.items} items on {args.date}")
    return 0


def cmd_progress_summary(store: Store, args: argparse.Namespace) -> int:
    totals: dict[tuple[str, str, str], int] = {}
    for p in store.list_progress():
        key = (p.data_type, p.purpose, p.batch)
        totals[key] = totals.get(key, 0) + p.items_completed
    _print_table(
        ["data_type", "purpose", "batch", "completed"],
        [[k[0], k[1], k[2], str(v)] for k, v in sorted(totals.items())],
    )
    if not totals:
        print("(no progress logged yet)")
    return 0


def cmd_plan(store: Store, args: argparse.Namespace) -> int:
    today = parse_date(args.today) if args.today else date.today()
    if today is None:
        raise StoreError("invalid --today date")
    report = build_plan(store, today)

    print(f"Plan as of {today.isoformat()}")
    print("Team rows (HC / throughput per day):")
    _print_table(
        ["from_date", "to_date", "total_hc", "throughput/day"],
        [
            [
                _fmt_date(r.from_date),
                _fmt_date(r.to_date),
                f"{r.total_hc:g}",
                f"{r.throughput_per_day:g}",
            ]
            for r in report.team.rows
        ],
    )
    s = report.snapshot
    print()
    print(
        f"Snapshot: required={s.total_required} completed={s.total_completed} "
        f"in_progress(remaining)={s.total_in_progress} ({s.pct_complete:.1f}%)"
    )
    print()
    _print_table(
        [
            "data_type",
            "purpose",
            "batch",
            "required",
            "completed",
            "%done",
            "assigned_hc",
            "capacity/day",
            "status",
            "start",
            "finish",
        ],
        [
            [
                lp.lane.data_type,
                lp.lane.purpose,
                lp.lane.batch,
                str(lp.lane.total_required),
                str(lp.completed),
                f"{lp.pct_complete:.1f}",
                f"{lp.lane.assigned_hc:g}",
                f"{lp.capacity_per_day:.1f}",
                lp.lane.status,
                _fmt_date(lp.start_date),
                _fmt_date(lp.finish_date),
            ]
            for lp in report.lanes
        ],
    )
    if report.warnings:
        print()
        print("Warnings:")
        for w in report.warnings:
            print(f"  - {w}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="annotation-planner")
    parser.add_argument(
        "--dir",
        default=os.environ.get("PLANNER_DATA_DIR", "data"),
        help="directory holding the CSV files (default: ./data, or PLANNER_DATA_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the CSV files").set_defaults(func=cmd_init)

    w = sub.add_parser("web", help="launch the web UI")
    w.add_argument("--host", default=os.environ.get("PLANNER_HOST", "127.0.0.1"))
    w.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or os.environ.get("PLANNER_PORT", "8000")),
    )
    w.set_defaults(func=cmd_web)

    p = sub.add_parser("team", help="manage date-ranged team HC and throughput")
    team_sub = p.add_subparsers(dest="team_command", required=True)
    a = team_sub.add_parser("add", help="add a row: team add [FROM] [TO] HC THROUGHPUT")
    a.add_argument("from_date", nargs="?", help="YYYY-MM-DD, optional (open start)")
    a.add_argument("to_date", nargs="?", help="YYYY-MM-DD, optional (open end)")
    a.add_argument("total_hc")
    a.add_argument("throughput", help="items per HC per day")
    a.set_defaults(func=cmd_team_add)
    team_sub.add_parser("list").set_defaults(func=cmd_team_list)
    team_sub.add_parser("clear").set_defaults(func=cmd_team_clear)

    p = sub.add_parser("sheet", help="link the Google Sheet")
    sh_sub = p.add_subparsers(dest="sheet_command", required=True)
    s = sh_sub.add_parser("set", help="set the spreadsheet ID (long string in the sheet URL)")
    s.add_argument("sheet_id")
    s.set_defaults(func=cmd_sheet_set)
    sh_sub.add_parser("show").set_defaults(func=cmd_sheet_show)

    sy = sub.add_parser("sync", help="pull data from the linked Google Sheet")
    sy.add_argument("--sheet-id", help="override the configured spreadsheet ID")
    sy.set_defaults(func=cmd_sync)

    p = sub.add_parser("lane", help="manage labeling tasks (type/purpose/batch)")
    lane_sub = p.add_subparsers(dest="lane_command", required=True)
    a = lane_sub.add_parser("add")
    a.add_argument("data_type", help=f"one of {', '.join(DATA_TYPES)}")
    a.add_argument("purpose")
    a.add_argument("batch", help="e.g. B1, B2")
    a.add_argument("required", help="total items required")
    a.add_argument("assigned_hc", help="HC assigned to this task")
    a.add_argument("status", nargs="?", default="not_started",
                   help=f"one of {', '.join(STATUSES)}")
    a.set_defaults(func=cmd_lane_add)
    lane_sub.add_parser("list").set_defaults(func=cmd_lane_list)
    s = lane_sub.add_parser("set", help="update a task (same fields as add)")
    s.add_argument("data_type")
    s.add_argument("purpose")
    s.add_argument("batch")
    s.add_argument("required")
    s.add_argument("assigned_hc")
    s.add_argument("status")
    s.set_defaults(func=cmd_lane_set)
    st = lane_sub.add_parser("status", help="change only the status of a task")
    st.add_argument("data_type")
    st.add_argument("purpose")
    st.add_argument("batch")
    st.add_argument("status", help=f"one of {', '.join(STATUSES)}")
    st.set_defaults(func=cmd_lane_status)
    d = lane_sub.add_parser("delete")
    d.add_argument("data_type")
    d.add_argument("purpose")
    d.add_argument("batch")
    d.set_defaults(func=cmd_lane_delete)

    p = sub.add_parser("progress", help="log and summarize completed work")
    pg_sub = p.add_subparsers(dest="pg_command", required=True)
    l = pg_sub.add_parser("log")
    l.add_argument("date", help="YYYY-MM-DD")
    l.add_argument("data_type")
    l.add_argument("purpose")
    l.add_argument("batch")
    l.add_argument("items")
    l.set_defaults(func=cmd_progress_log)
    pg_sub.add_parser("summary").set_defaults(func=cmd_progress_summary)

    pl = sub.add_parser("plan", help="show the full plan report")
    pl.add_argument("--today", help="planning date, YYYY-MM-DD (default: today)")
    pl.set_defaults(func=cmd_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(Path(args.dir))
    if args.func is not cmd_init:
        store.init()  # auto-create the CSV files so every command just works
    try:
        return args.func(store, args)
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
