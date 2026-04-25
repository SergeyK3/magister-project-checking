# Headless launcher for magistrcheckbot (used by Task Scheduler).
#
# Differences from bot_start.ps1:
#   - no interactive console required;
#   - python writes its own log to state\logs\bot.log via FileHandler
#     (configured by magister_checking.bot.app.configure_logging when
#     BOT_LOG_FILE is set in env);
#   - exits with code 0 if a bot is already running, so Task Scheduler
#     does not treat that as a failure.
#
# Why no PowerShell redirect (2>&1 | Out-File): Python logging.basicConfig
# writes INFO records to stderr by default. PowerShell 5.x with
# $ErrorActionPreference = 'Stop' wraps every stderr line of a native
# command as a NativeCommandError and kills the pipeline on the first
# log line. The bot then never gets to run_polling. Letting Python
# write the file itself avoids that pitfall and is portable.
#
# Registered as the Action of the scheduled task created by
# scripts\install_bot_task.ps1. Manual invocation is normally not needed.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

try { chcp 65001 > $null } catch { }
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

# Refuse to start a second polling process: two pollers with the same
# token break Telegram getUpdates.
$existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'magister_checking\s+bot' }

if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ', '
    Write-Host "Bot already running (PID $pids), exiting headless launcher with 0."
    exit 0
}

$pythonExe = 'python'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} else {
    $venvPython2 = Join-Path $repoRoot 'venv\Scripts\python.exe'
    if (Test-Path $venvPython2) { $pythonExe = $venvPython2 }
}

$logsDir = Join-Path $repoRoot 'state\logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}
$logFile = Join-Path $logsDir 'bot.log'

$header = "=== bot started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')  launcher_pid=$PID  python=$pythonExe ==="
Add-Content -Path $logFile -Value $header -Encoding utf8

# UTF-8 + unbuffered: Cyrillic in logs stays intact, FileHandler flushes
# each record so we do not lose tail on a crash.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
# Tells configure_logging() to attach a FileHandler that writes to this file.
$env:BOT_LOG_FILE = $logFile

# Run python directly: no stdout/stderr capture in PowerShell. The bot
# writes its own log lines via Python logging (StreamHandler -> the
# hidden console, dropped; FileHandler -> $logFile). Pre-logging stderr
# (e.g. ConfigError print before configure_logging runs) is dropped in
# headless mode by design - the footer with non-zero exit_code below
# tells you to re-run the bot in foreground (scripts\bot_start.ps1) to
# see those messages.
& $pythonExe -m magister_checking bot

$exitCode = $LASTEXITCODE

$footer = "=== bot exited: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')  exit_code=$exitCode ==="
Add-Content -Path $logFile -Value $footer -Encoding utf8

exit $exitCode
