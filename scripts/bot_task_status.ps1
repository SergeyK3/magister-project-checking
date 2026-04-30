# Shows the status of the MagistrcheckbotAutoStart scheduled task and
# correlates it with the actually running python bot processes.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\bot_task_status.ps1

[CmdletBinding()]
param(
    [string]$TaskName = 'MagistrcheckbotAutoStart'
)

$ErrorActionPreference = 'Stop'

try { chcp 65001 > $null } catch { }
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Write-Host "=== Task Scheduler ==="
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Task '$TaskName' NOT registered."
    Write-Host "To register: powershell -ExecutionPolicy Bypass -File scripts\install_bot_task.ps1"
} else {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    [PSCustomObject]@{
        TaskName            = $task.TaskName
        State               = $task.State
        LastRunTime         = $info.LastRunTime
        LastTaskResult      = $info.LastTaskResult
        LastResultHex       = ('0x{0:X}' -f $info.LastTaskResult)
        NextRunTime         = $info.NextRunTime
        NumberOfMissedRuns  = $info.NumberOfMissedRuns
    } | Format-List
}

Write-Host "=== Bot processes ==="
$bot = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'magister_checking\s+bot' }
if ($bot) {
    $bot | ForEach-Object {
        '{0}  PID={1}  PPID={2}  CMD={3}' -f $_.CreationDate, $_.ProcessId, $_.ParentProcessId, $_.CommandLine
    }
} else {
    Write-Host "No bot processes running."
}

Write-Host ""
Write-Host "=== Last 20 log lines ==="
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logFile = Join-Path $repoRoot 'state\logs\bot.log'
if (Test-Path $logFile) {
    Write-Host "Log file: $logFile"
    Get-Content -Path $logFile -Tail 20
} else {
    Write-Host "Log file not found: $logFile"
}
