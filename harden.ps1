# StudyLock - one-time hardening. Right-click -> "Run with PowerShell as Administrator".
#
# It does three things:
#   1. registers the guard as a scheduled task that runs elevated, at every logon,
#      and re-checks once a minute - so killing it buys you at most 60 seconds
#   2. lets the guard blackhole distracting domains in the hosts file during a
#      session, which covers every browser and every profile at once
#   3. turns off Incognito/InPrivate and guest profiles, which would otherwise
#      run without the extension
#
# Nothing here starts a session. Nothing here can shorten one.

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click harden.ps1 -> Run as administrator, or run:" -ForegroundColor Yellow
    Write-Host "  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-File','$PSCommandPath'"
    Read-Host "Press Enter to close"
    exit 1
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Definition
$guard = Join-Path $base "guard.py"
$pythonw = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $candidate = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if ($candidate) { $pythonw = $candidate } else { throw "pythonw.exe not found - install Python or edit this script." }
}

Write-Host "StudyLock hardening" -ForegroundColor Cyan
Write-Host "  python : $pythonw"
Write-Host "  guard  : $guard"

# ---------------------------------------------------------------- 1. task
$taskName = "StudyLock Guard"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}" A' -f $guard) -WorkingDirectory $base

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
             -RepetitionInterval (New-TimeSpan -Minutes 1) `
             -RepetitionDuration (New-TimeSpan -Days 3650)

$principalDef = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                  -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($atLogon, $repeat) `
    -Principal $principalDef -Settings $settings -Force | Out-Null
Write-Host "  [ok] scheduled task '$taskName' registered (elevated, at logon, re-checked every minute)" -ForegroundColor Green

# ------------------------------------------------------- 2/3. browser policy
function Set-Policy($path, $name, $value) {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    New-ItemProperty -Path $path -Name $name -Value $value -PropertyType DWord -Force | Out-Null
}

$chrome = "HKLM:\SOFTWARE\Policies\Google\Chrome"
Set-Policy $chrome "IncognitoModeAvailability" 1   # incognito disabled
Set-Policy $chrome "BrowserGuestModeEnabled" 0
Set-Policy $chrome "BrowserAddPersonEnabled" 0
Write-Host "  [ok] Chrome: incognito, guest mode and new profiles disabled" -ForegroundColor Green

$edge = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"
Set-Policy $edge "InPrivateModeAvailability" 1
Set-Policy $edge "BrowserGuestModeEnabled" 0
Set-Policy $edge "BrowserAddProfileEnabled" 0
Write-Host "  [ok] Edge: InPrivate, guest mode and new profiles disabled" -ForegroundColor Green

Write-Host ""
Write-Host "Hardening done. Restart Chrome/Edge for the policies to apply." -ForegroundColor Cyan
Write-Host "The hosts-file layer will now switch on automatically during sessions."
Read-Host "Press Enter to close"
