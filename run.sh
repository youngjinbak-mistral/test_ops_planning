#!/usr/bin/env bash
# One-command local launch (macOS/Linux). Double-click equivalent on Windows: run.bat
set -euo pipefail
cd "$(dirname "$0")"
DIR="${PLANNER_DATA_DIR:-data}"
HOST="${PLANNER_HOST:-127.0.0.1}"
PORT="${PLANNER_PORT:-8000}"
python3 -m planner.cli --dir "$DIR" init
python3 -m planner.cli --dir "$DIR" web --host "$HOST" --port "$PORT"
