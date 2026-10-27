"""Command-line interface for the annotation planner.

Usage:
    python -m planner.cli --dir data <command> [args...]

Commands:
    init                          create the CSV files in --dir
    operator add ID NAME THROUGHPUT [--inactive]
    operator list
    operator set-throughput ID THROUGHPUT
    operator deactivate ID | operator activate ID
    requirement set DATA_TYPE PURPOSE BATCH TOTAL
    requirement list
    requirement delete DATA_TYPE PURPOSE BATCH
    allocate set DATA_TYPE PURPOSE BATCH OPERATOR_ID FRACTION [FROM_DATE] [TO_DATE]
    allocate clear DATA_TYPE PURPOSE BATCH OPERATOR_ID
    allocate list
    progress log DATE DATA_TYPE PURPOSE BATCH ITEMS
    progress summary              completed items by type/purpose/batch
    plan [--today YYYY-MM-DD] [--horizon DAYS]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .planning import build_plan, completion_summary
from .store import (
    DATA_TYPES,
    Allocation,
    Operator,
    ProgressEntry,
    Requirement,
    Store,
    StoreError,
    parse_date,
    validate_batch,
    validate_data_type,
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


def cmd_operator_add(store: Store, args: argparse.Namespace) -> int:
    throughput = float(args.throughput)
    if throughput <= 0:
        raise StoreError("throughput must be > 0")
    store.upsert_operator(
        Operator(
            operator_id=args.operator_id,
            name=args.name,
            throughput_per_day=throughput,
            active=not args.inactive,
        )
    )
    print(f"operator {args.operator_id} saved")
    return 0


def cmd_operator_list(store: Store, args: argparse.Namespace) -> int:
    ops = store.list_operators()
    _print_table(
        ["operator_id", "name", "throughput/day", "active", "capacity/day"],
        [
            [
                op.operator_id,
                op.name,
                f"{op.throughput_per_day:g}",
                "yes" if op.active else "no",
                f"{op.throughput_per_day:g}" if op.active else "0",
            ]
            for op in ops
        ],
    )
    return 0


def cmd_operator_set_throughput(store: Store, args: argparse.Namespace) -> int:
    op = store.get_operator(args.operator_id)
    throughput = float(args.throughput)
    if throughput <= 0:
        raise StoreError("throughput must be > 0")
    store.upsert_operator(
        Operator(op.operator_id, op.name, throughput, op.active)
    )
    print(f"operator {args.operator_id} throughput set to {throughput:g}/day")
    return 0


def cmd_operator_set_active(store: Store, args: argparse.Namespace) -> int:
    op = store.get_operator(args.operator_id)
    store.upsert_operator(
        Operator(op.operator_id, op.name, op.throughput_per_day, args.active)
    )
    print(f"operator {args.operator_id} {'activated' if args.active else 'deactivated'}")
    return 0


def cmd_requirement_set(store: Store, args: argparse.Namespace) -> int:
    data_type = validate_data_type(args.data_type)
    batch = validate_batch(args.batch)
    total = int(args.total)
    store.set_requirement(Requirement(data_type, args.purpose, batch, total))
    print(f"requirement set: {data_type}/{args.purpose}/{batch} = {total} items")
    return 0


def cmd_requirement_list(store: Store, args: argparse.Namespace) -> int:
    reqs = store.list_requirements()
    _print_table(
        ["data_type", "purpose", "batch", "total_required"],
        [
            [r.data_type, r.purpose, r.batch, str(r.total_required)]
            for r in sorted(reqs, key=lambda r: (r.data_type, r.purpose, r.batch))
        ],
    )
    return 0


def cmd_requirement_delete(store: Store, args: argparse.Namespace) -> int:
    store.delete_requirement(
        validate_data_type(args.data_type), args.purpose, validate_batch(args.batch)
    )
    print("requirement deleted")
    return 0


def cmd_allocate_set(store: Store, args: argparse.Namespace) -> int:
    fraction = float(args.fraction)
    from_date = parse_date(args.from_date, "from_date") if args.from_date else None
    to_date = parse_date(args.to_date, "to_date") if args.to_date else None
    store.set_allocation(
        Allocation(
            data_type=validate_data_type(args.data_type),
            purpose=args.purpose,
            batch=validate_batch(args.batch),
            operator_id=args.operator_id,
            hc_fraction=fraction,
            from_date=from_date,
            to_date=to_date,
        )
    )
    print(f"allocation set: {args.operator_id} -> {args.data_type}/{args.purpose}/{args.batch} at {fraction:g} HC")
    return 0


def cmd_allocate_clear(store: Store, args: argparse.Namespace) -> int:
    store.clear_allocation(
        validate_data_type(args.data_type),
        args.purpose,
        validate_batch(args.batch),
        args.operator_id,
    )
    print("allocation cleared")
    return 0


def cmd_allocate_list(store: Store, args: argparse.Namespace) -> int:
    allocs = store.list_allocations()
    _print_table(
        ["data_type", "purpose", "batch", "operator_id", "hc_fraction", "from", "to"],
        [
            [
                a.data_type,
                a.purpose,
                a.batch,
                a.operator_id,
                f"{a.hc_fraction:g}",
                _fmt_date(a.from_date),
                _fmt_date(a.to_date),
            ]
            for a in sorted(
                allocs, key=lambda a: (a.data_type, a.purpose, a.batch, a.operator_id)
            )
        ],
    )
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
    rows = completion_summary(store)
    _print_table(
        ["data_type", "purpose", "batch", "completed", "first_day", "last_day"],
        [
            [
                str(r["data_type"]),
                str(r["purpose"]),
                str(r["batch"]),
                str(r["completed"]),
                str(r["first_day"]),
                str(r["last_day"]),
            ]
            for r in rows
        ],
    )
    if not rows:
        print("(no progress logged yet)")
    return 0


def cmd_plan(store: Store, args: argparse.Namespace) -> int:
    today = parse_date(args.today) if args.today else date.today()
    if today is None:
        raise StoreError("invalid --today date")
    lanes, utilization, warnings = build_plan(store, today, args.horizon)

    print(f"Plan as of {today.isoformat()}")
    print()
    _print_table(
        [
            "data_type",
            "purpose",
            "batch",
            "required",
            "completed",
            "%done",
            "capacity/day",
            "start",
            "finish",
        ],
        [
            [
                b.key.data_type,
                b.key.purpose,
                b.key.batch,
                str(b.total_required),
                str(b.completed),
                f"{b.pct_complete:.1f}",
                f"{b.capacity_per_day:.1f}",
                _fmt_date(b.start_date),
                _fmt_date(b.finish_date),
            ]
            for lane in lanes
            for b in lane.batches
        ],
    )
    print()
    active_ops = [op for op in store.list_operators() if op.active]
    total_hc = len(active_ops)
    peak = max((u.allocated_hc for u in utilization), default=0.0)
    print(f"Active HC: {total_hc} operator(s); peak allocated HC across horizon: {peak:.2f}")
    if warnings:
        print()
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("No warnings.")
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

    p = sub.add_parser("operator", help="manage operators")
    op_sub = p.add_subparsers(dest="op_command", required=True)
    a = op_sub.add_parser("add")
    a.add_argument("operator_id")
    a.add_argument("name")
    a.add_argument("throughput", help="items per annotator per day")
    a.add_argument("--inactive", action="store_true")
    a.set_defaults(func=cmd_operator_add)
    op_sub.add_parser("list").set_defaults(func=cmd_operator_list)
    s = op_sub.add_parser("set-throughput")
    s.add_argument("operator_id")
    s.add_argument("throughput")
    s.set_defaults(func=cmd_operator_set_throughput)
    d = op_sub.add_parser("deactivate")
    d.add_argument("operator_id")
    d.set_defaults(func=lambda st, ar: cmd_operator_set_active(st, argparse.Namespace(operator_id=ar.operator_id, active=False)))
    ac = op_sub.add_parser("activate")
    ac.add_argument("operator_id")
    ac.set_defaults(func=lambda st, ar: cmd_operator_set_active(st, argparse.Namespace(operator_id=ar.operator_id, active=True)))

    p = sub.add_parser("requirement", help="manage required labeling volumes")
    req_sub = p.add_subparsers(dest="req_command", required=True)
    s = req_sub.add_parser("set", help=f"set required items; data_type in {', '.join(DATA_TYPES)}")
    s.add_argument("data_type")
    s.add_argument("purpose")
    s.add_argument("batch", help="e.g. B1, B2")
    s.add_argument("total", help="total items required")
    s.set_defaults(func=cmd_requirement_set)
    req_sub.add_parser("list").set_defaults(func=cmd_requirement_list)
    d = req_sub.add_parser("delete")
    d.add_argument("data_type")
    d.add_argument("purpose")
    d.add_argument("batch")
    d.set_defaults(func=cmd_requirement_delete)

    p = sub.add_parser("allocate", help="manage HC allocation")
    al_sub = p.add_subparsers(dest="al_command", required=True)
    s = al_sub.add_parser("set")
    s.add_argument("data_type")
    s.add_argument("purpose")
    s.add_argument("batch")
    s.add_argument("operator_id")
    s.add_argument("fraction", help="HC fraction in (0, 1]")
    s.add_argument("from_date", nargs="?", help="YYYY-MM-DD, optional")
    s.add_argument("to_date", nargs="?", help="YYYY-MM-DD, optional")
    s.set_defaults(func=cmd_allocate_set)
    c = al_sub.add_parser("clear")
    c.add_argument("data_type")
    c.add_argument("purpose")
    c.add_argument("batch")
    c.add_argument("operator_id")
    c.set_defaults(func=cmd_allocate_clear)
    al_sub.add_parser("list").set_defaults(func=cmd_allocate_list)

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
    pl.add_argument("--horizon", type=int, default=365, help="HC utilization horizon in days")
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
