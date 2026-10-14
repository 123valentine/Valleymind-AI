<#
  create-shortcut.ps1 - one-time local setup for the ValleyMind Windows desktop
  companion. It performs two things and nothing else:

    1. Generates desktop\companion.ico (a branded icon for the shortcut).
    2. Creates a shortcut named "ValleyMind Companion" on the current user's
       Desktop that launches the EXISTING Electron shell. That shell starts the
       existing Flask backend (python app.py) via the project's own Python
       environment; no global install and no production config is touched.

  Run it with:
    powershell -ExecutionPolicy Bypass -File desktop\create-shortcut.ps1
#>
[CmdletBinding()]
param(
  [string]$ShortcutName = "ValleyMind Companion"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$desktopDir  = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopDir
$electronExe = Join-Path $desktopDir "node_modules\electron\dist\electron.exe"
$iconPath    = Join-Path $desktopDir "companion.ico"

# ── 1. Branded icon ─────────────────────────────────────────────────────────
function New-CompanionIcon {
  param([string]$OutPath)
  $size = 256
  $bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.Clear([System.Drawing.Color]::Transparent)

  $rect = New-Object System.Drawing.Rectangle(6, 6, ($size - 12), ($size - 12))
  $c1 = [System.Drawing.Color]::FromArgb(255, 110, 168, 255)
  $c2 = [System.Drawing.Color]::FromArgb(255, 36, 66, 130)
  $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, $c1, $c2, 55.0)
  $g.FillEllipse($brush, $rect)

  $rim = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(120, 210, 228, 255), 5)
  $g.DrawEllipse($rim, $rect)

  $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White, 34)
  $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.EndCap   = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $pts = @(
    (New-Object System.Drawing.PointF(74, 84)),
    (New-Object System.Drawing.PointF(128, 186)),
    (New-Object System.Drawing.PointF(182, 84))
  )
  $g.DrawLines($pen, $pts)

  # PNG payload wrapped in an ICO container (PNG-in-ICO; fine on Vista+).
  $ms = New-Object System.IO.MemoryStream
  $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  $png = $ms.ToArray()
  $ms.Dispose(); $g.Dispose(); $bmp.Dispose()

  $fs = [System.IO.File]::Create($OutPath)
  $bw = New-Object System.IO.BinaryWriter($fs)
  $bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]1)      # ICONDIR
  $bw.Write([Byte]0);  $bw.Write([Byte]0);  $bw.Write([Byte]0); $bw.Write([Byte]0)  # 256x256
  $bw.Write([UInt16]1); $bw.Write([UInt16]32)
  $bw.Write([UInt32]$png.Length)
  $bw.Write([UInt32]22)
  $bw.Write($png)
  $bw.Flush(); $bw.Dispose(); $fs.Dispose()
}

New-CompanionIcon -OutPath $iconPath
Write-Host "Icon written:     $iconPath"

# ── 2. Desktop shortcut ─────────────────────────────────────────────────────
$userDesktop  = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $userDesktop "$ShortcutName.lnk"

if (Test-Path $electronExe) {
  $target = $electronExe
  $arguments = "."
} else {
  Write-Warning "Electron not found at $electronExe - the shortcut will use the .cmd launcher instead."
  $target = Join-Path $desktopDir "launch-companion.cmd"
  $arguments = ""
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcutPath)
$lnk.TargetPath       = $target
if ($arguments -ne "") { $lnk.Arguments = $arguments }
$lnk.WorkingDirectory = $desktopDir
$lnk.IconLocation     = "$iconPath,0"
$lnk.Description      = "Open the ValleyMind desktop companion"
$lnk.Save()

Write-Host "Shortcut created: $shortcutPath"
Write-Host "Target:           $target $arguments"
Write-Host "Working dir:      $desktopDir"
