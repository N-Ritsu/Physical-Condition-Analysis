@echo off
rem pushd also works when this folder is opened from a network path (UNC).
pushd "%~dp0"
rem Use the bundled Python (python folder) if present, otherwise the PC Python.
if exist "python\pythonw.exe" (
  start "" "python\pythonw.exe" run_dashboard.py
) else (
  start "" pythonw run_dashboard.py
)
