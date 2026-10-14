<#
  install-companion.ps1 - one-time local setup for the ValleyMind desktop
  companion's automatic start.

  It does exactly two things:

    1. Registers the small background controller (desktop\controller.py) to run
       at sign-in, using the CURRENT USER's Startup folder. This is per-user,
       needs no administrator rights, shows up in Task Manager > Startup apps,
       and is removed cleanly by uninstall-companion.ps1.
    2. Starts that controller right now, so the website's "Show companion
       outside ValleyMind" toggle works immediately without signing out first.

  No global install, no service, no scheduled task, no registry Run key, and no
  production configuration is touched. The controller only listens on
  127.0.0.1:8737 and only launches the Electron companion when you ask it to.

  Run it with:
    powershell -ExecutionPolicy Bypass -File desktop\install-companion.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$desktopDir  = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopDir
$controller  = Join-Path $desktopDir "controller.py"
$iconPath    = Join-Path $desktopDir "companion.ico"
$startupDir  = [Environment]::GetFolderPath("Startup")
$lnkPath     = Join-Path $startupDir "ValleyMind Companion Controller.lnk"

if (-not (Test-Path $controller)) { throw "controller.py not found at $controller" }

# ── Locate a Python that can run the controller (stdlib only) ───────────────
function Find-PythonWindowed {
  $candidates = @(
    (Join-Path $projectRoot "env311\Scripts\pythonw.exe"),
    (Join-Path $projectRoot "env\Scripts\pythonw.exe")
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

$pythonw = Find-PythonWindowed
if (-not $pythonw) {
  throw "Python was not found. Install Python 3, or create the project's env311 virtual environment, then run this again."
}

# ── 1. Register at sign-in (per-user Startup folder) ────────────────────────
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = '"' + $controller + '"'
$lnk.WorkingDirectory = $desktopDir
if (Test-Path $iconPath) { $lnk.IconLocation = "$iconPath,0" }
$lnk.Description = "ValleyMind Companion background controller (starts the desktop companion on demand)"
$lnk.Save()
Write-Host "Registered at sign-in: $lnkPath"
Write-Host "  Python:     $pythonw"
Write-Host "  Controller: $controller"

# ── 2. Start it now ─────────────────────────────────────────────────────────
$already = $false
try {
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8737/state" -UseBasicParsing -TimeoutSec 2
  if ($resp.StatusCode -eq 200) { $already = $true }
} catch { $already = $false }

if ($already) {
  Write-Host "Controller is already running."
} else {
  # Quote the script path: the user profile can contain spaces
  # (e.g. "C:\Users\John Doe\..."), and an unquoted path would make Python
  # exit silently before it ever reaches controller.py.
  Start-Process -FilePath $pythonw -ArgumentList @('"' + $controller + '"') -WorkingDirectory $desktopDir -WindowStyle Hidden
  $started = $false
  for ($i = 0; $i -lt 20 -and -not $started; $i++) {
    Start-Sleep -Milliseconds 400
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8737/state" -UseBasicParsing -TimeoutSec 2
      if ($resp.StatusCode -eq 200) { $started = $true }
    } catch { }
  }
  if ($started) { Write-Host "Controller started. Ready at http://127.0.0.1:8737" }
  else { Write-Warning "Controller did not answer yet; it may still be starting." }
}

Write-Host ""
Write-Host "Done. The website's Settings toggle can now start the companion on demand."
