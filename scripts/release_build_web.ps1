<#
.SYNOPSIS
  Release build of the Studio web UI into web/out (tracked in git, as main expects).

.DESCRIPTION
  End-user PCs have no Node: the updater (StudioUpdateCore.ps1) restores the prebuilt UI with
  `git checkout origin/<branch> -- web/out web/STUDIO_VERSION`. So every UI release must commit
  a fresh web/out. This script:
    1. waits for C:\Users\<user>\Desktop\build.lock (one Next build at a time on the PC);
    2. runs `npm ci` (if node_modules is missing) and `npm run build` in web/;
    3. checks web/out/index.html exists;
    4. gates on scripts/merge_checks/check_providers.py --out web/out (no provider names);
    5. stages web/out + web/STUDIO_VERSION (`git add -A`). Commits only with -Commit. Never pushes.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release_build_web.ps1
  powershell -ExecutionPolicy Bypass -File scripts\release_build_web.ps1 -Commit -Message "UI: release"
#>
param(
    [switch]$Commit,
    [string]$Message = "web/out: release build",
    [switch]$SkipProviderCheck,
    [string]$LockPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'build.lock')
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root 'web'

function Get-BuildLock {
    $t0 = Get-Date
    while ($true) {
        try {
            $fs = [IO.File]::Open($LockPath, 'CreateNew', 'Write', 'None')
            $w = New-Object IO.StreamWriter($fs); $w.Write("$Root $PID $(Get-Date -Format o)"); $w.Close()
            return
        } catch {
            if ((Test-Path $LockPath) -and ((Get-Date) - (Get-Item $LockPath).LastWriteTime).TotalMinutes -gt 20) {
                Remove-Item $LockPath -Force -ErrorAction SilentlyContinue; continue
            }
            if (((Get-Date) - $t0).TotalMinutes -gt 30) { throw "build.lock wait timeout ($LockPath)" }
            Start-Sleep 5
        }
    }
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) { throw 'npm not found: install Node.js 20+ to make a release build' }

Get-BuildLock
try {
    Push-Location $Web
    if (-not (Test-Path (Join-Path $Web 'node_modules'))) {
        Write-Host '==> npm ci' -ForegroundColor Cyan
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    }
    Write-Host '==> npm run build' -ForegroundColor Cyan
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' }
} finally {
    Pop-Location
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}

$idx = Join-Path $Web 'out\index.html'
if (-not (Test-Path $idx)) { throw 'web/out/index.html missing after build' }

if (-not $SkipProviderCheck) {
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    Write-Host '==> check_providers (web/out)' -ForegroundColor Cyan
    & $py (Join-Path $Root 'scripts\merge_checks\check_providers.py') --out (Join-Path $Web 'out')
    if ($LASTEXITCODE -ne 0) { throw 'provider names found in web/out - release aborted' }
}

Write-Host '==> git add web/out web/STUDIO_VERSION' -ForegroundColor Cyan
& git -C $Root add -A -- web/out web/STUDIO_VERSION
if ($Commit) {
    & git -C $Root commit -m $Message -- web/out web/STUDIO_VERSION
    if ($LASTEXITCODE -ne 0) { Write-Host 'nothing to commit' -ForegroundColor Yellow }
}
Write-Host 'OK: web/out built and staged (not pushed).' -ForegroundColor Green
