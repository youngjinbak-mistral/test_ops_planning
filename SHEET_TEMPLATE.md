# Annotation Planner — Google Sheet setup (one file, 3 tabs)

Spreadsheet: **Annotation Planner — Data**
ID: `10Dh9vSTWi54LDXo9ug_iIVBws-3gMq0YbGa9wETdvz8`
Link: https://docs.google.com/spreadsheets/d/10Dh9vSTWi54LDXo9ug_iIVBws-3gMq0YbGa9wETdvz8/edit

## Step 1 — Create the 3 tabs

At the bottom of the spreadsheet, rename `Sheet1` to `team`, then add two more tabs named `lanes` and `progress`.

## Step 2 — Paste the headers

**Tab `team`** (row 1):

```
from_date,to_date,total_hc,throughput_per_day
```

Leave `from_date`/`to_date` empty for an open-ended period. Rows must not overlap.
Example:

```
from_date,to_date,total_hc,throughput_per_day
2025-01-01,2025-01-31,4,40
2025-02-01,,2,55
```

**Tab `lanes`** (row 1):

```
data_type,purpose,batch,total_required,assigned_hc,status
```

`data_type` is one of `Eval`, `FT`, `Test`. `batch` looks like `B1`, `B2`. `status` is one of `not_started`, `in_progress`, `completed`, `stopped`.
Example:

```
data_type,purpose,batch,total_required,assigned_hc,status
Eval,support,B1,500,2,in_progress
Eval,support,B2,300,2,not_started
FT,qa,B1,1000,1,in_progress
```

**Tab `progress`** (row 1):

```
date,data_type,purpose,batch,items_completed
```

Example:

```
date,data_type,purpose,batch,items_completed
2025-01-06,Eval,support,B1,80
2025-01-07,Eval,support,B1,120
```

## Step 3 — Share the sheet

Click **Share** → **Change** → **Anyone with the link** → **Viewer** → **Done**.

(The tool only reads the sheet; it never writes to it.)

## Step 4 — Connect it to the tool

In the web UI, open the **Sync** page, paste the sheet ID
(`10Dh9vSTWi54LDXo9ug_iIVBws-3gMq0YbGa9wETdvz8`), click **Save**, then **Pull from sheet**.

Or on the command line:

```
python -m planner.cli sheet set 10Dh9vSTWi54LDXo9ug_iIVBws-3gMq0YbGa9wETdvz8
python -m planner.cli sync
```
