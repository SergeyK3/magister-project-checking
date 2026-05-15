# Registers a Task Scheduler job that auto-starts magistrcheckbot at the
# current user's logon. Idempotent: if the task already exists it is replaced.
#
# No admin rights required: the task runs only while the user is logged on
# (LogonType = Interactive, RunLevel = Limited). This means the bot survives
# closing Cursor / the PowerShell window, but does NOT survive a logout.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_bot_task.ps1
#
# After install the task takes effect on the next logon. To start it
# immediately without re-logging in:
#   Start-ScheduledTask -TaskName MagistrcheckbotAutoStart

[CmdletBinding()]
param(
    [string]$TaskName = 'MagistrcheckbotAutoStart'
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$headlessScript = Join-Path $repoRoot 'scripts\bot_run_headless.ps1'

if (-not (Test-Path $headlessScript)) {
    Write-Error "Headless launcher not found: $headlessScript"
    exit 2
}

$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

# -NoProfile + -ExecutionPolicy Bypass so the task does not depend on the
# user's profile or the current execution policy.
$psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$headlessScript`""
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument $psArgs `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user

# RestartCount/RestartInterval: auto-restart on non-zero exit code (e.g.
# transient network failure). 3 attempts, 1 minute apart.
# ExecutionTimeLimit: cmdlets do not expose PT0S; 365 days is effectively
# unlimited for a long-running bot.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew

# LogonType Interactive + RunLevel Limited = task runs without a stored
# password and without admin rights. Sufficient because the bot only
# writes inside its own repo and talks to Telegram/Google over HTTPS.
$principal = New-ScheduledTaskPrincipal `
    -UserId $user `
    -LogonType Interactive `
    -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$TaskName' already exists - replacing."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Auto-start magistrcheckbot Telegram bot at user logon. See scripts/install_bot_task.ps1.' | Out-Null

Write-Host "Task '$TaskName' registered."
Write-Host "  User:           $user"
Write-Host "  Trigger:        AtLogOn"
Write-Host "  Action:         powershell.exe $psArgs"
Write-Host "  WorkingDir:     $repoRoot"
Write-Host "  Restart policy: 3 attempts, 1 min interval"
Write-Host ""
Write-Host "Run now (without re-logging in):"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Status:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\bot_task_status.ps1"
Write-Host "Remove:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\uninstall_bot_task.ps1"
