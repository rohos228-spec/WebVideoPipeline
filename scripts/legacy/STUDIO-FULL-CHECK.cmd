@echo off
cd /d "%~dp0"
echo.
echo  Полная проверка Studio — см. docs\archive\FULL-VERIFICATION.md
echo  (6-9 часов: каждая кнопка, нода, пайплайн, генерации)
echo.
echo  Быстрый автоматический базис (~10 мин):
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\guardian\run-everything.ps1"
echo.
start "" "%~dp0docs\archive\FULL-VERIFICATION.md"
pause
