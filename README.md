# Annotation Planner

A spreadsheet-backed tool to plan, execute, and amend data labeling
(annotation) work — with a **web dashboard** for non-technical users.
Python 3 standard library only — no dependencies, no install step.

## Concepts

- **Data type**: `Eval`, `FT` (fine-tuning), or `Test`.
- **Purpose**: free-form label describing why the data is labeled (e.g. `qc`, `safety`).
- **Batch**: `B1`, `B2`, ... Batches within one (data type, purpose) group run
  **sequentially**; different groups run in parallel.
- **Task (lane)**: one row = one batch broken down by data type and purpose,
  with: required items, assigned HC, and a status.
- **Status**: `not_started`, `in_progress`, `completed`, `stopped`.
  Completed/stopped tasks are excluded from scheduling.
- **Team**: total HC (operators) and one throughput (items per HC per day).
  **Capacity/day for a task = assigned HC x throughput.**

## Launch the web UI

```bash
# 1. get the code (from GitHub: click Code -> Download ZIP, or:)
git clone https://github.com/youngjinbak-mistral/test_ops_planning.git
cd test_ops_planning

# 2. create the data folder
python3 -m planner.cli --dir data init

# 3. set your team: total HC and throughput (items per HC per day)
python3 -m planner.cli --dir data team set 4 40

# 4. start the UI
python3 -m planner.cli --dir data web --port 8000
```

Open **http://127.0.0.1:8000** in a browser. Press `Ctrl+C` in the terminal to stop.

The UI has four pages (links at the top):

| Page | What you can do |
|------|-----------------|
| **Dashboard** | Snapshot cards (total required / completed / in progress / overall %), the task breakdown table (one row per type/purpose/batch with required, completed, %, assigned HC, capacity/day, status, planned start/finish), and a daily grid with one column per date showing items completed that day. Warnings appear when assigned HC exceeds total HC or a task has no HC. |
| **Team & throughput** | Edit total HC and throughput. |
| **Tasks (lanes)** | Add a task (type, purpose, batch, required, assigned HC, status); edit or delete any row inline. |
| **Log progress** | Log items completed per day per task; see the history. |

## Data files

All state lives in plain CSV files inside the data directory — they are the
source of truth and can be edited directly in Excel/LibreOffice/Google Sheets
(save as "CSV (Comma delimited)"):

| File | Columns |
|------|---------|
| `team.csv` | total_hc, throughput_per_day (single row) |
| `lanes.csv` | data_type, purpose, batch, total_required, assigned_hc, status |
| `progress.csv` | date, data_type, purpose, batch, items_completed |

## CLI (same data, for scripting)

```bash
python3 -m planner.cli --dir data init
python3 -m planner.cli --dir data team set 4 40
python3 -m planner.cli --dir data team show
python3 -m planner.cli --dir data lane add Eval qc B1 200 2 in_progress
python3 -m planner.cli --dir data lane list
python3 -m planner.cli --dir data lane set Eval qc B1 250 2 in_progress
python3 -m planner.cli --dir data lane status Eval qc B1 stopped
python3 -m planner.cli --dir data lane delete Eval qc B1
python3 -m planner.cli --dir data progress log 2025-01-08 Eval qc B1 35
python3 -m planner.cli --dir data progress summary
python3 -m planner.cli --dir data plan [--today 2025-01-08]
python3 -m planner.cli --dir data web --port 8000
```

`plan` prints the same numbers as the dashboard: team, snapshot, per-task
breakdown with capacity and schedule, and warnings.

## Scheduling rules

- Within one (data type, purpose) group, batches run in order: `B2` starts the
  day after `B1` is scheduled to finish.
- A task is scheduled only if its status is `not_started` or `in_progress`,
  it has remaining work, and it has assigned HC > 0.
- Finish date = start + ceil(remaining / capacity) - 1 days.
- The dashboard's daily grid columns cover all logged dates plus the full
  scheduled date range.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
