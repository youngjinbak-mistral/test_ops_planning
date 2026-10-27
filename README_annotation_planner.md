# Annotation Planner

A spreadsheet-backed CLI tool to plan, execute, and amend data labeling
(annotation) work. Python 3 standard library only — no dependencies.

## Concepts

- **Data type**: `Eval`, `FT` (fine-tuning), or `Test`.
- **Purpose**: free-form label describing why the data is labeled (e.g. `qc`, `safety`, `edge-case`).
- **Batch**: `B1`, `B2`, ... Batches within one (data type, purpose) lane run
  **sequentially**; different lanes run in parallel.
- **Operator**: an annotator with an individual throughput (items/day).
- **Allocation**: an operator's HC fraction (`0 < fraction <= 1`) assigned to a
  lane batch, optionally limited to a date window. Capacity/day for a batch =
  sum of `fraction x throughput` over its allocations.

## Data files

`init` creates a directory with four CSV files. They are the source of truth
and can be edited directly in Excel/LibreOffice/Google Sheets:

| File | Columns |
|------|---------|
| `operators.csv` | operator_id, name, throughput_per_day, active |
| `requirements.csv` | data_type, purpose, batch, total_required |
| `allocations.csv` | data_type, purpose, batch, operator_id, hc_fraction, from_date, to_date |
| `progress.csv` | date, data_type, purpose, batch, items_completed |

## Usage

```bash
cd annotation_planner

# set up a project directory
python3 -m planner.cli --dir data init

# operators (feature 2: HC and capacity)
python3 -m planner.cli --dir data operator add op1 Alice 40
python3 -m planner.cli --dir data operator add op2 Bob 20
python3 -m planner.cli --dir data operator list

# requirements (feature 1: how much labeling is required)
python3 -m planner.cli --dir data requirement set Eval qc B1 200
python3 -m planner.cli --dir data requirement set Eval qc B2 100
python3 -m planner.cli --dir data requirement set FT train B1 150
python3 -m planner.cli --dir data requirement list

# HC allocation (feature 3)
python3 -m planner.cli --dir data allocate set Eval qc B1 op1 1.0
python3 -m planner.cli --dir data allocate set Eval qc B2 op2 1.0
python3 -m planner.cli --dir data allocate list

# daily progress (feature 5)
python3 -m planner.cli --dir data progress log 2025-01-06 Eval qc B1 40
python3 -m planner.cli --dir data progress summary

# full plan (features 1-5: required vs completed, capacity, schedule, warnings)
python3 -m planner.cli --dir data plan --today 2025-01-08
```

## Amending the plan (feature 4)

- Change volume: `requirement set <type> <purpose> <batch> <new_total>` (upsert)
  or edit `requirements.csv` directly.
- Change HC: `operator set-throughput`, `operator deactivate/activate`,
  `allocate set/clear`, or edit `operators.csv` / `allocations.csv`.
- Every `plan` run recomputes capacity, remaining work, start/finish dates,
  and HC warnings from the current files.

## Warnings

The `plan` command reports:

- batches with no capacity (unscheduled; they block later batches in the lane),
- operators allocated above 1.0 HC,
- days where total allocated HC exceeds active team HC.

## Tests

```bash
cd annotation_planner
python3 -m unittest discover -s tests -v
```
