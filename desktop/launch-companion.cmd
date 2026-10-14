@echo off
setlocal EnableExtensions
rem ============================================================
rem  ValleyMind Companion - fallback launcher / troubleshooter
rem
rem  Normal use needs nothing here: the website's Settings toggle
rem  ("Show companion outside ValleyMind") starts the companion through
rem  the background controller (desktop\controller.py), which is set up
rem  once with desktop\install-companion.ps1.
rem
rem  Double-click THIS file only to start the companion directly, e.g.
rem  to watch startup diagnostics. It starts the existing Electron shell,
rem  which in turn starts the existing Flask backend (python app.py)
rem  using the project's own Python environment.
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
