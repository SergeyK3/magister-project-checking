# Запускает magistrcheckbot в текущем окне PowerShell.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts\bot_start.ps1
#
# Что делает:
#   1. Переходит в корень репозитория.
#   2. Проверяет, не запущен ли уже `python -m magister_checking bot` —
#      второй polling-процесс с тем же токеном ломает getUpdates.
#   3. Использует python из .venv, если он есть, иначе системный python.
#   4. Запускает бота в foreground — закрытие окна останавливает процесс.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# UTF-8 для консоли: иначе Write-Host с кириллицей показывает кракозябры
# в локалях Windows с ANSI-кодировкой 1251/866. chcp 65001 и смена OutputEncoding
# действуют только в пределах этого процесса PowerShell.
try { chcp 65001 > $null } catch { }
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'magister_checking\s+bot' }

if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ', '
    Write-Warning "Бот уже запущен (PID $pids). Остановите его через scripts\bot_stop.ps1."
    exit 1
}

$pythonExe = 'python'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} else {
    $venvPython2 = Join-Path $repoRoot 'venv\Scripts\python.exe'
    if (Test-Path $venvPython2) { $pythonExe = $venvPython2 }
}

Write-Host "Запускаю magistrcheckbot"
Write-Host "  repo:   $repoRoot"
Write-Host "  python: $pythonExe"
Write-Host "Остановить: Ctrl+C в этом окне или scripts\bot_stop.ps1 из другого."
Write-Host ""

& $pythonExe -m magister_checking bot
exit $LASTEXITCODE
