# Annotation Planner

A spreadsheet-backed tool to plan, execute, and amend data labeling
(annotation) work — with a **web dashboard** for non-technical users.
Python 3 standard library only — no dependencies, no install step.
Styled with Mistral brand assets.

## Concepts

- **Data type**: `Eval`, `FT` (fine-tuning), or `Test`.
- **Purpose**: free-form label describing why the data is labeled (e.g. `qc`, `safety`).
- **Batch**: `B1`, `B2`, ... Batches within one (data type, purpose) group run
  **sequentially**; different groups run in parallel.
- **Task (lane)**: one row = one batch broken down by data type and purpose,
  with: required items, assigned HC, and a status.
- **Status**: `not_started`, `in_progress`, `completed`, `stopped`.
  Completed/stopped tasks are excluded from scheduling.
- **Team rows**: the team's total HC and throughput (items per HC per day)
  are set **per date range**, so HC and throughput can change over time
  (e.g. 4 operators at 40 items/HC/day in January, 2 operators at 55 from
  February on). Rows must not overlap; leave a date empty for an open-ended
  period. **Capacity/day for a task = assigned HC x throughput on that day.**

## Deploy (no terminal needed after this)

### Option A — Render (free, recommended)

1. Go to https://dashboard.render.com → **New +** → **Blueprint**.
2. Connect the repository; Render reads `render.yaml` automatically.
3. Click **Apply**. Render builds and gives you a public URL like
   `https://annotation-planner.onrender.com`.
4. Open the URL — the dashboard is live.

**Port**: Render injects a `PORT` environment variable (usually 10000) and the
app listens on it automatically — no port configuration needed.

**How data persists on the free tier** (no paid disk): the app pulls your
Google Sheet into memory **on every start** (`PLANNER_SYNC_ON_START=1` in
`render.yaml`). So:

- Set the environment variable `ANNOTATION_SHEET_ID` to your sheet ID
  (service → **Environment** tab → **Add Environment Variable**), then
  redeploy (Manual Deploy → Deploy latest commit).
- Your sheet is the source of truth. Anything you log in the UI lives until
  the service restarts or redeploys (free tier spins down after ~15 min idle
  and wipes its local state); after a restart the sheet is re-pulled.
- Logging the same day twice in the UI adds to that day's number instead of
  creating a duplicate row, so a sheet re-pull never double-counts.

### Option B — Docker (any server / your own machine)

```bash
docker build -t annotation-planner .
docker run -p 8000:8000 -v planner-data:/app/data annotation-planner
```

Open http://localhost:8000 (or the server's address).

### Option C — Run locally (one command)

- macOS/Linux: `./run.sh`
- Windows: double-click `run.bat`

Then open http://127.0.0.1:8000.

### Option D — Any Python 3 host

```bash
python3 -m planner.cli web --port 8000
```

## Use it

Open the URL. The UI has five pages (links at the top):

| Page | What you can do |
|------|-----------------|
| **Dashboard** | Snapshot cards (total required / completed / in progress / overall %, peak team HC, throughput), the task breakdown table (one row per type/purpose/batch with required, completed, %, **Total HC**, **Assigned HC**, **Throughput**, capacity/day, status, planned start/finish), and a daily grid with one column per date showing items completed that day. Warnings appear when assigned HC exceeds the team's HC or a task has no capacity. |
| **Team** | Add date-ranged team rows (from/to dates, total HC, throughput). Overlapping rows are rejected with an explanation. Clear all rows if you need to re-enter them. |
| **Tasks** | Add a task (type, purpose, batch, required, assigned HC, status); edit or delete any row inline. |
| **Progress** | Log items completed per day per task; see the history. |
| **Sync** | Connect one Google Sheet and pull it in. |

## Google Sheets sync — ONE spreadsheet

Edit your data in a single Google Sheet; the tool pulls it in. Create **one
spreadsheet** in your Drive with **three tabs** named exactly `team`, `lanes`,
and `progress`, with these columns:

| Tab | Columns |
|-----|---------|
| `team` | from_date, to_date, total_hc, throughput_per_day |
| `lanes` | data_type, purpose, batch, total_required, assigned_hc, status |
| `progress` | date, data_type, purpose, batch, items_completed |

Share the spreadsheet as **Anyone with the link → Viewer** (the tool only
reads; it never writes). See `SHEET_TEMPLATE.md` for ready-to-paste headers
and examples.

Wire it up (pick one):

```bash
python3 -m planner.cli sheet set <SHEET_ID>
python3 -m planner.cli sync
```

Or set the environment variable `ANNOTATION_SHEET_ID=<SHEET_ID>`, or use the
**Sync** page in the web UI — paste the ID, click **Save**, then
**Pull from sheet**.

The sheet ID is the long string in the sheet's URL:
`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`

Merge rules: `team` and `lanes` are **replaced** from the sheet; `progress`
is **merged** (items summed per date + task), so entries logged through the
web UI are never lost.

## Data files

All state lives in plain CSV files inside the data directory — they are the
source of truth and can be edited directly in Excel/LibreOffice/Google Sheets
(save as "CSV (Comma delimited)"):

| File | Columns |
|------|---------|
| `team.csv` | from_date, to_date, total_hc, throughput_per_day |
| `lanes.csv` | data_type, purpose, batch, total_required, assigned_hc, status |
| `progress.csv` | date, data_type, purpose, batch, items_completed |

Configuration:

- `PLANNER_DATA_DIR` — where the CSVs live (default `./data`)
- `PLANNER_HOST` / `PLANNER_PORT` — web UI bind address (default `127.0.0.1:8000`);
  the standard `PORT` variable (set by Render, Fly, Heroku, ...) takes precedence
- `ANNOTATION_SHEET_ID` — default Google Sheet for sync

## CLI (same data, for scripting)

```bash
python3 -m planner.cli init
python3 -m planner.cli team add 2025-01-01 2025-01-31 4 40   # bounded period
python3 -m planner.cli team add 2025-02-01 "" 2 55           # open-ended (quote empty)
python3 -m planner.cli team list
python3 -m planner.cli team clear
python3 -m planner.cli lane add Eval qc B1 200 2 in_progress
python3 -m planner.cli lane list
python3 -m planner.cli lane set Eval qc B1 250 2 in_progress
python3 -m planner.cli lane status Eval qc B1 stopped
python3 -m planner.cli lane delete Eval qc B1
python3 -m planner.cli progress log 2025-01-08 Eval qc B1 35
python3 -m planner.cli progress summary
python3 -m planner.cli plan [--today 2025-01-08]
python3 -m planner.cli sheet set <SHEET_ID>
python3 -m planner.cli sheet show
python3 -m planner.cli sync
python3 -m planner.cli web --port 8000
```

`plan` prints the same numbers as the dashboard: team rows, snapshot,
per-task breakdown with capacity and schedule, and warnings.

## Scheduling rules

- Within one (data type, purpose) group, batches run in order: `B2` starts the
  day after `B1` is scheduled to finish.
- A task is scheduled only if its status is `not_started` or `in_progress`,
  it has remaining work, and it has assigned HC > 0.
- Capacity is recomputed day by day from the team row covering that day, so a
  task pauses on days with no team row and resumes when the team returns.
- The dashboard's daily grid columns cover all logged dates plus the full
  scheduled date range.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same suite on every push via `.github/workflows/tests.yml`.
