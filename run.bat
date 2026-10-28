@echo off
REM One-command local launch (Windows). Double-click this file.
cd /d "%~dp0"
if not defined PLANNER_DATA_DIR set PLANNER_DATA_DIR=data
if not defined PLANNER_HOST set PLANNER_HOST=127.0.0.1
if not defined PLANNER_PORT set PLANNER_PORT=8000
python -m planner.cli --dir "%PLANNER_DATA_DIR%" init
python -m planner.cli --dir "%PLANNER_DATA_DIR%" web --host %PLANNER_HOST% --port %PLANNER_PORT%
pause
