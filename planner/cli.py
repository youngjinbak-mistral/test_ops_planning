"""Command-line interface for the annotation planner.

Usage:
    python3 -m planner.cli --dir data <command> [args...]

Commands:
    init                          create the CSV files in --dir
    sheet set PREFIX              link Google Sheets by title ('<PREFIX> - team/lanes/progress')
    sheet set-id TAB SHEET_ID     link one spreadsheet by its exact ID (team|lanes|progress)
    sheet show
    sync [--prefix PREFIX]        pull data from the linked Google Sheets
    web [--host H] [--port P]     launch the web UI (default 127.0.0.1:8000)
    team set HC THROUGHPUT        set total HC and throughput (items/HC/day)
    team show
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
    Team,
    parse_date,
    validate_batch,
    validate_data_type,
    validate_status,
)


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else "-"


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


def cmd_sheet_set(store: Store, args: argparse.Namespace) -> int:
    from .sheets import set_sheet_prefix

    set_sheet_prefix(store, args.prefix)
    print(f"sheet prefix set: {args.prefix!r}")
    print("Expected sheet titles: '<prefix> - team', '<prefix> - lanes', '<prefix> - progress'")
    return 0


def cmd_sheet_set_id(store: Store, args: argparse.Namespace) -> int:
    from .sheets import set_sheet_id

    set_sheet_id(store, args.suffix, args.sheet_id)
    print(f"sheet id for '{args.suffix}' set: {args.sheet_id}")
    return 0


def cmd_sheet_show(store: Store, args: argparse.Namespace) -> int:
    from .sheets import get_sheet_ids, get_sheet_prefix

    prefix = get_sheet_prefix(store)
    ids = get_sheet_ids(store)
    print(f"sheet prefix: {prefix or '(not configured)'}")
    for suffix in ("team", "lanes", "progress"):
        print(f"sheet id [{suffix}]: {ids.get(suffix, '(not set)')}")
    return 0


def cmd_sync(store: Store, args: argparse.Namespace) -> int:
    from .sheets import pull

    result = pull(store, args.prefix)
    print(f"synced from sheets (prefix {result.source!r}):")
    print(f"  team: {'updated' if result.team_synced else 'no row found'}")
    print(f"  lanes: {result.lanes_replaced} replaced")
    print(
        f"  progress: {result.progress_added} new, "
        f"{result.progress_updated} merged into existing day(s)"
    )
    return 0


def cmd_web(store: Store, args: argparse.Namespace) -> int:
    from .web import run

    run(store, host=args.host, port=args.port)
    return 0


def cmd_team_set(store: Store, args: argparse.Namespace) -> int:
    store.set_team(
        Team(
            total_hc=float(args.total_hc),
            throughput_per_day=float(args.throughput),
        )
    )
    print(
        f"team set: {float(args.total_hc):g} HC at "
        f"{float(args.throughput):g} items/HC/day"
    )
    return 0


def cmd_team_show(store: Store, args: argparse.Namespace) -> int:
    team = store.get_team()
    print(f"total HC: {team.total_hc:g}")
    print(f"throughput: {team.throughput_per_day:g} items/HC/day")
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
        [
            [k[0], k[1], k[2], str(v)]
            for k, v in sorted(totals.items())
        ],
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
    print(
        f"Team: {report.team.total_hc:g} HC at "
        f"{report.team.throughput_per_day:g} items/HC/day"
    )
    s = report.snapshot
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
        default="data",
        help="directory holding the CSV files (default: ./data)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the CSV files").set_defaults(func=cmd_init)

    p = sub.add_parser("sheet", help="configure the Google Sheets source")
    sheet_sub = p.add_subparsers(dest="sheet_command", required=True)
    s = sheet_sub.add_parser("set", help="set the sheet title prefix, e.g. 'Annotation Planner Data'")
    s.add_argument("prefix")
    s.set_defaults(func=cmd_sheet_set)
    si = sheet_sub.add_parser("set-id", help="set the exact spreadsheet ID for one table (team|lanes|progress)")
    si.add_argument("suffix", help="team, lanes, or progress")
    si.add_argument("sheet_id", help="the long ID from the spreadsheet URL")
    si.set_defaults(func=cmd_sheet_set_id)
    sheet_sub.add_parser("show").set_defaults(func=cmd_sheet_show)

    sy = sub.add_parser("sync", help="pull data from the linked Google Sheets")
    sy.add_argument("--prefix", help="override the configured sheet title prefix")
    sy.set_defaults(func=cmd_sync)

    w = sub.add_parser("web", help="launch the web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.set_defaults(func=cmd_web)

    p = sub.add_parser("team", help="manage team HC and throughput")
    team_sub = p.add_subparsers(dest="team_command", required=True)
    s = team_sub.add_parser("set", help="set total HC and throughput per HC per day")
    s.add_argument("total_hc")
    s.add_argument("throughput")
    s.set_defaults(func=cmd_team_set)
    team_sub.add_parser("show").set_defaults(func=cmd_team_show)

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
