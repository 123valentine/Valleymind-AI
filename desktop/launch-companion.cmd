@echo off
setlocal EnableExtensions
rem ============================================================
rem  ValleyMind Companion - fallback launcher / troubleshooter
rem
rem  The Desktop shortcut "ValleyMind Companion" launches electron.exe
rem  directly (no console window). Double-click THIS file only if that
rem  shortcut is missing, or if you want to watch startup diagnostics.
rem
rem  It starts the existing Electron shell, which in turn starts the
rem  existing Flask backend (python app.py) using the project's own
rem  Python environment.
rem ============================================================
cd /d "%~dp0"

set "ELECTRON=%~dp0node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" (
  echo.
  echo  [ValleyMind] Electron is not installed in this project.
  echo  Install it once by running, in this folder:
  echo.
  echo      npm install
  echo.
  pause
  exit /b 1
)

rem Match main.js findPython(): prefer the project's own virtual environments.
if exist "%~dp0..\env311\Scripts\python.exe" set "VM_PYTHON=%~dp0..\env311\Scripts\python.exe"
if not defined VM_PYTHON if exist "%~dp0..\env\Scripts\python.exe" set "VM_PYTHON=%~dp0..\env\Scripts\python.exe"

echo [ValleyMind] Starting companion...
start "" "%ELECTRON%" .
exit /b 0
