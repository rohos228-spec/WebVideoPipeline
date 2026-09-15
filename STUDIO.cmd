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

rem Обновление кода: только перемотка вперёд.
rem
rem Здесь стояло `git reset --hard origin/main` — оно молча стирало любые
rem правки оператора на его машине при каждом запуске с main. Теперь
rem `merge --ff-only`: если локально есть свои коммиты, обновление просто
rem не состоится и скажет об этом, а работа останется.
rem
rem Отсюда же убрана загрузка `scripts/studio.ps1` с
rem raw.githubusercontent.com из стороннего форка: скачанный файл в запуске
rem не участвует с тех пор, как точкой входа стал `scripts/run-studio.ps1`,
rem то есть это было исполнение чужого кода без ревью и без нужды.
echo.
echo Studio: updating code...
where git >nul 2>&1
if %ERRORLEVEL%==0 (
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
