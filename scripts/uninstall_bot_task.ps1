# Removes the Task Scheduler auto-start job for magistrcheckbot.
# Does NOT stop the running bot process; for that use scripts\bot_stop.ps1.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall_bot_task.ps1

[CmdletBinding()]
param(
    [string]$TaskName = 'MagistrcheckbotAutoStart'
)

$ErrorActionPreference = 'Stop'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' not found - nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Task '$TaskName' removed."
Write-Host "Bot process in current session (if any) is NOT stopped."
Write-Host "To stop it: powershell -ExecutionPolicy Bypass -File scripts\bot_stop.ps1"
