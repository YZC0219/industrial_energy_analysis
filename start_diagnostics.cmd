@echo off
cd /d "%~dp0"
title Industrial Energy Diagnostics
echo Starting workbench: http://127.0.0.1:8010/diagnostics
echo Keep this window open while using the workbench.
if not exist "%~dp0tmp\diagnostics_runtime" mkdir "%~dp0tmp\diagnostics_runtime"
set "TEMP=%~dp0tmp\diagnostics_runtime"
set "TMP=%TEMP%"
python -m tools.start_diagnostics --mode auto --port 8010
pause
