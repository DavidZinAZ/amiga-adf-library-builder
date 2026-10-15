@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" -c "from pathlib import Path; from amiga_adf_library_builder.gui.app import run; raise SystemExit(run(base_dir=Path.cwd()))"
if errorlevel 1 pause
endlocal
