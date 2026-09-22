@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Web Studio
chcp 65001 >nul 2>&1

if "%STUDIO_HEALED%"=="1" goto :start

rem No auto-update here on purpose: local edits must never be wiped by
rem a plain start. Updating the code is an explicit menu action only
rem (item [4] in scripts/studio.ps1). Just hand over to powershell.
goto :start

:start
set "STUDIO_PS1=%~dp0scripts\studio.ps1"
set "VP_REPO_ROOT=%~dp0"
if "%VP_REPO_ROOT:~-1%"=="\" set "VP_REPO_ROOT=%VP_REPO_ROOT:~0,-1%"

where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Error code %ERR%.
    pause
)
exit /b %ERR%
