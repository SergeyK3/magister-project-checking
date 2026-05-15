# Останавливает все процессы `python -m magister_checking bot`.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts\bot_stop.ps1
#
# Что делает:
#   1. Ищет python-процессы, у которых в CommandLine есть `magister_checking bot`.
#   2. Корректно просит их завершиться (Stop-Process без -Force).
#   3. Если за TimeoutSeconds не завершились — добивает через -Force.

[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 10
)

$ErrorActionPreference = 'Stop'

# UTF-8 для консоли: иначе Write-Host с кириллицей показывает кракозябры
# в локалях Windows с ANSI-кодировкой 1251/866.
try { chcp 65001 > $null } catch { }
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'magister_checking\s+bot' }
}

$processes = @(Get-BotProcesses)
if ($processes.Count -eq 0) {
    Write-Host "Бот не запущен: процессы 'python -m magister_checking bot' не найдены."
    exit 0
}

foreach ($proc in $processes) {
    Write-Host "Останавливаю бот PID $($proc.ProcessId)..."
    try {
        Stop-Process -Id $proc.ProcessId -ErrorAction Stop
    } catch {
        Write-Warning "Не удалось мягко остановить PID $($proc.ProcessId): $($_.Exception.Message)"
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $still = @(Get-BotProcesses)
    if ($still.Count -eq 0) {
        Write-Host "Бот остановлен."
        exit 0
    }
    Start-Sleep -Milliseconds 300
}

$still = @(Get-BotProcesses)
if ($still.Count -gt 0) {
    $pids = ($still | ForEach-Object { $_.ProcessId }) -join ', '
    Write-Warning "Процессы бота всё ещё активны ($pids), завершаю принудительно."
    foreach ($proc in $still) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

$final = @(Get-BotProcesses)
if ($final.Count -gt 0) {
    $pids = ($final | ForEach-Object { $_.ProcessId }) -join ', '
    Write-Error "Не удалось остановить бот. Осталось живых PID: $pids."
    exit 1
}

Write-Host "Бот остановлен."
exit 0
