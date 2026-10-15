@echo off
setlocal
rem ── ValleyMind desktop app (unified, single window) ─────────────────────────
rem Launches the ONE ValleyMind desktop application: the main app window plus
rem the built-in floating companion. There is no separate companion app to
rem install -- the Settings toggle "Show companion outside ValleyMind" turns the
rem floating window on and off.
rem
rem The companion-only mode (started automatically by desktop/controller.py when
rem the hosted WEBSITE toggle is used) still exists; this script intentionally
rem runs the full app with --app.
set "ELECTRON=%~dp0node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" (
  echo Electron is not installed in this project.
  echo Run "npm install" inside the desktop folder once, then try again.
  pause
  exit /b 1
)
start "" "%ELECTRON%" "%~dp0" --app
