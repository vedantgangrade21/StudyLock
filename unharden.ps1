# StudyLock - removes the hardening installed by harden.ps1.
# It refuses to run while a session is in progress: this is an uninstaller,
# not an escape hatch.

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this as Administrator." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Definition
$statePath = Join-Path $base "state.json"
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    $left = [int]$state.end_wall - [int][double]::Parse((Get-Date -UFormat %s))
    if ($state.active -and $left -gt 0) {
        Write-Host ""
        Write-Host "A study session is still running - $([math]::Ceiling($left / 60)) minutes left." -ForegroundColor Yellow
        Write-Host "Come back when it has finished." -ForegroundColor Yellow
        Read-Host "Press Enter to close"; exit 1
    }
}

Unregister-ScheduledTask -TaskName "StudyLock Guard" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "  [ok] scheduled task removed" -ForegroundColor Green

foreach ($p in @(
    @{ Path = "HKLM:\SOFTWARE\Policies\Google\Chrome";  Names = @("IncognitoModeAvailability","BrowserGuestModeEnabled","BrowserAddPersonEnabled") },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Names = @("InPrivateModeAvailability","BrowserGuestModeEnabled","BrowserAddProfileEnabled") }
)) {
    foreach ($n in $p.Names) {
        Remove-ItemProperty -Path $p.Path -Name $n -ErrorAction SilentlyContinue
    }
}
Write-Host "  [ok] browser policies cleared" -ForegroundColor Green

$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$text = Get-Content $hosts -Raw
if ($text -match "StudyLock BEGIN") {
    $clean = ($text -split "`r?`n" | Where-Object { $_ -notmatch "StudyLock (BEGIN|END)" }) -join "`n"
    $lines = @(); $skip = $false
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match "StudyLock BEGIN") { $skip = $true; continue }
        if ($line -match "StudyLock END")   { $skip = $false; continue }
        if (-not $skip) { $lines += $line }
    }
    Set-Content -Path $hosts -Value ($lines -join "`r`n") -Encoding ASCII
    ipconfig /flushdns | Out-Null
    Write-Host "  [ok] hosts file restored" -ForegroundColor Green
}

Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "StudyLock" -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "StudyLock hardening removed." -ForegroundColor Cyan
Read-Host "Press Enter to close"
