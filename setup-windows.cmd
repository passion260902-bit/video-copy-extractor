@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3.11 scripts\setup_runtime.py
    exit /b
  )
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3.12 scripts\setup_runtime.py
    exit /b
  )
  py -3.13 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3.13 scripts\setup_runtime.py
    exit /b
  )
)
python scripts\setup_runtime.py
exit /b
