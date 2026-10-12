# StudyLock - first-time setup. Right-click -> Run with PowerShell (no admin needed).
#
# Finds Python, creates your personal config from the template, and puts a
# StudyLock shortcut on the Desktop. Run harden.ps1 afterwards (as admin) if you
# want the stronger protections.

$ErrorActionPreference = "Stop"
$base = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "StudyLock setup" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. Python
$pythonw = $null
foreach ($candidate in @(
    (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source,
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe")
)) {
    if ($candidate -and (Test-Path $candidate)) { $pythonw = $candidate; break }
}
if (-not $pythonw) {
    $py = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if ($py) {
        $found = & $py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"
        if (Test-Path $found) { $pythonw = $found }
    }
}
if (-not $pythonw) {
    Write-Host "  [x] Python not found. Install Python 3.11+ from python.org, then run this again." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
$version = & (Join-Path (Split-Path $pythonw) "python.exe") --version
Write-Host "  [ok] $version at $pythonw" -ForegroundColor Green

# ------------------------------------------------------------- 2. your config
$config = Join-Path $base "config.json"
if (Test-Path $config) {
    Write-Host "  [ok] config.json already exists - leaving your settings alone" -ForegroundColor Green
} else {
    Copy-Item (Join-Path $base "config.example.json") $config
    Write-Host "  [ok] created config.json from the template" -ForegroundColor Green
}

# ---------------------------------------------------------------- 3. shortcut
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop "StudyLock.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = '"{0}"' -f (Join-Path $base "studylock.pyw")
$s.WorkingDirectory = $base
$s.IconLocation = "$env:SystemRoot\System32\shell32.dll,43"
$s.Description = "Start a locked study session"
$s.Save()
Write-Host "  [ok] shortcut on your Desktop" -ForegroundColor Green

Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. Open SETUP.html and follow the three steps."
Write-Host "  2. Load the 'extension' folder in chrome://extensions (Developer mode -> Load unpacked)."
Write-Host "  3. Add your Discord server IDs in the app's settings screen."
Write-Host "  4. Optional but recommended: right-click harden.ps1 -> Run as administrator."
Write-Host ""
Write-Host "Start StudyLock from the Desktop shortcut whenever you want a session."
Read-Host "Press Enter to close"
