@echo off
rem Двойной клик = остановить magistrcheckbot, показать результат и закрыться по клавише.
set "SCRIPT_DIR=%~dp0"
powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%bot_stop.ps1"
pause
