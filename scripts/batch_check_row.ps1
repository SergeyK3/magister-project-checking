# Пакетный прогон check-row по диапазону строк листа «Регистрация».
#
# Usage (из корня репозитория):
#   powershell -ExecutionPolicy Bypass -File scripts\batch_check_row.ps1 -StartRow 5 -EndRow 12
#   powershell -ExecutionPolicy Bypass -File scripts\batch_check_row.ps1 -StartRow 18 -EndRow 18 -Apply
#   powershell -ExecutionPolicy Bypass -File scripts\batch_check_row.ps1 -StartRow 2 -EndRow 10 -Apply -OnlyIfChanged

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$StartRow,
    [Parameter(Mandatory = $true)]
    [int]$EndRow,
    [switch]$Apply,
    [switch]$OnlyIfChanged,
    [switch]$SkipHttp
)

$ErrorActionPreference = 'Stop'

try { chcp 65001 > $null } catch { }
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

if ($EndRow -lt $StartRow) {
    throw "EndRow ($EndRow) must be >= StartRow ($StartRow)"
}

$failed = [System.Collections.Generic.List[int]]::new()
for ($r = $StartRow; $r -le $EndRow; $r++) {
    $argList = [System.Collections.Generic.List[string]]::new()
    $argList.Add('-m')
    $argList.Add('magister_checking')
    $argList.Add('check-row')
    $argList.Add('--row')
    $argList.Add("$r")
    if ($Apply) { $argList.Add('--apply') }
    if ($OnlyIfChanged) { $argList.Add('--only-if-changed') }
    if ($SkipHttp) { $argList.Add('--skip-http') }

    Write-Host ""
    Write-Host "=== check-row --row $r ===" -ForegroundColor Cyan
    & python $argList.ToArray()
    if ($LASTEXITCODE -ne 0) {
        [void]$failed.Add($r)
    }
}

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "Done. All rows completed with exit code 0."
    exit 0
}
Write-Host "Failed rows: $($failed -join ', ')" -ForegroundColor Yellow
exit 1
