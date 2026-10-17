# Acquire/release the shared frontend build lock (C:\Users\Admin\Desktop\build.lock).
# Usage: .\scripts\build-lock.ps1 acquire [-Owner A] [-TimeoutSec 1800]; .\scripts\build-lock.ps1 release
param(
  [Parameter(Mandatory=$true)][ValidateSet('acquire','release','status')][string]$Action,
  [string]$Owner = $env:COMPUTERNAME,
  [int]$TimeoutSec = 1800,
  [string]$LockPath = (Join-Path $env:USERPROFILE 'Desktop\build.lock')
)
$ErrorActionPreference = 'Stop'
switch ($Action) {
  'acquire' {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
      try {
        $fs = [IO.File]::Open($LockPath, 'CreateNew', 'Write', 'None')
        $b = [Text.Encoding]::UTF8.GetBytes("$Owner $PID $(Get-Date -Format o)")
        $fs.Write($b, 0, $b.Length); $fs.Close()
        Write-Output "build.lock acquired by $Owner"; exit 0
      } catch [IO.IOException] {
        if ((Get-Date) -gt $deadline) { Write-Error "build.lock busy: $(Get-Content $LockPath -ErrorAction SilentlyContinue)"; exit 1 }
        $age = (Get-Date) - (Get-Item $LockPath -ErrorAction SilentlyContinue).LastWriteTime
        if ($age.TotalMinutes -gt 60) { Remove-Item $LockPath -Force -ErrorAction SilentlyContinue; continue }
        Start-Sleep -Seconds 5
      }
    }
  }
  'release' { Remove-Item $LockPath -Force -ErrorAction SilentlyContinue; Write-Output 'build.lock released' }
  'status'  { if (Test-Path $LockPath) { Get-Content $LockPath } else { 'free' } }
}