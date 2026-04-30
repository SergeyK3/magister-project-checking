@echo off
rem Двойной клик = запуск magistrcheckbot в новом окне PowerShell.
set "SCRIPT_DIR=%~dp0"
powershell -NoExit -ExecutionPolicy Bypass -File "%SCRIPT_DIR%bot_start.ps1"
