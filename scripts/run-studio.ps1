param(
    [switch]$NoRun
)

# Запуск Web Studio в один клик
$ErrorActionPreference = "Continue"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
try { chcp 65001 | Out-Null } catch { }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "             VIDEO PIPELINE WEB STUDIO                      " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Папка: $Root" -ForegroundColor DarkGray
Write-Host ""

if ($NoRun) { return }

# Фоновый вотчер: как только бэкенд отдаст 200 на /api/health — открывает браузер
Write-Host "==> [1/2] Запуск авто-открытия браузера (http://127.0.0.1:8765/pipeline)..." -ForegroundColor Gray
$watcherCmd = "for (`$i=0; `$i -lt 60; `$i++) { Start-Sleep -Milliseconds 600; try { `$r = Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 1; if (`$r.StatusCode -eq 200) { Start-Process 'http://127.0.0.1:8765/pipeline'; break } } catch {} }"
Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", $watcherCmd

Write-Host "==> [2/2] Запуск сервера бэкенда..." -ForegroundColor Green
Write-Host ""

$backendScript = Join-Path $PSScriptRoot "run-backend.ps1"
& $backendScript

