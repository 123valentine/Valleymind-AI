<#
  uninstall-companion.ps1 - reverses install-companion.ps1.

  It:
    1. Stops the running background controller (and the companion it launched).
    2. Removes the per-user Startup entry.

  It does NOT delete any project files. To re-enable automatic start, run
  install-companion.ps1 again.

  Run it with:
    powershell -ExecutionPolicy Bypass -File desktop\uninstall-companion.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$desktopDir  = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopDir
$startupDir  = [Environment]::GetFolderPath("Startup")
$lnkPath     = Join-Path $startupDir "ValleyMind Companion Controller.lnk"

# ── 1. Ask the controller to stop (it also stops any companion it launched) ──
$python = (Join-Path $projectRoot "env311\Scripts\python.exe")
if (-not (Test-Path $python)) { $python = (Join-Path $projectRoot "env\Scripts\python.exe") }
if (-not (Test-Path $python)) {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { $python = $cmd.Source }
}

if ($python -and (Test-Path $python)) {
  try {
    & $python (Join-Path $desktopDir "controller.py") --stop
  } catch {
    Write-Warning "Could not signal the controller to stop: $($_.Exception.Message)"
  }
} else {
  Write-Warning "Python not found; skipping the stop request."
}

# ── 2. Remove the Startup entry ─────────────────────────────────────────────
if (Test-Path $lnkPath) {
  Remove-Item -LiteralPath $lnkPath -Force
  Write-Host "Removed Startup entry: $lnkPath"
} else {
  Write-Host "No Startup entry found (nothing to remove)."
}

Write-Host "Done. Automatic start is disabled."
