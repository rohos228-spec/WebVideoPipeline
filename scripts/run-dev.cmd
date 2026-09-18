@echo off
rem Dev-режим: бэкенд (воркер+API :8765) и фронт (Next :3000 с реврайтами на API).
rem Фронт ходит в API бэкенда, пересборка web/out не нужна.
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [run-dev] No .venv. First: python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e ".[dev]"
  pause
  exit /b 1
)
where pnpm >nul 2>nul
if errorlevel 1 (
  echo [run-dev] No pnpm. Install: npm install -g pnpm@10.33.3
  pause
  exit /b 1
)
echo [run-dev] Backend :8765 ...
start "video-pipeline backend (dev)" /d "%CD%" .venv\Scripts\python.exe -m app.main
echo [run-dev] Frontend :3000 ...
start "video-pipeline frontend (dev)" /d "%CD%\web" pnpm dev
echo [run-dev] Open http://127.0.0.1:3000  (API: http://127.0.0.1:8765/api/health)
