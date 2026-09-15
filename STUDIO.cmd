@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Web Studio
chcp 65001 >nul 2>&1

if "%STUDIO_HEALED%"=="1" goto :start

rem In development (any non-main branch or STUDIO_DEV=1), skip auto-update
if "%STUDIO_DEV%"=="1" goto :start
where git >nul 2>&1
if %ERRORLEVEL% equ 0 (
  for /f "tokens=*" %%b in ('git branch --show-current 2^>nul') do (
    if not "%%b"=="main" goto :start
  )
)

rem Self-heal and update. ASCII only: this file must decode as ASCII
rem (tests/test_studio_launcher_encoding.py) and PS 5.1 mangles em-dashes.
rem
rem 1) Launcher scripts are restored FROM GIT, not downloaded from a fork.
rem    A broken scripts/*.ps1 cannot be parsed by -File, so it is checked out
rem    again before powershell starts. It used to be fetched over HTTP from a
rem    third-party fork instead: unreviewed code executed on every start, and
rem    since run-studio.ps1 became the entry point that download was not even
rem    used.
rem
rem 2) Code update is fast-forward only. It used to be a hard reset onto the
rem    remote branch, which silently wiped any local edit on the operator
rem    machine at every start from main. With --ff-only an update simply does
rem    not happen when local commits exist, and says so.
echo.
echo Studio: healing launcher and updating code...
where git >nul 2>&1
if %ERRORLEVEL%==0 (
  git checkout -- scripts/run-studio.ps1 scripts/studio.ps1 2>nul
  git fetch origin main
  if %ERRORLEVEL%==0 (
    git merge --ff-only origin/main || echo Studio: local commits present, update skipped
  )
)

set STUDIO_HEALED=1
call "%~f0" %*
exit /b %ERRORLEVEL%

:start
set "STUDIO_PS1=%~dp0scripts\run-studio.ps1"
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
