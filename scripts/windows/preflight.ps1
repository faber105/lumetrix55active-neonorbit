[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$configFile = Join-Path $baseRoot 'config\worker.env'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv missing. Run scripts\windows\bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $configFile)) {
    throw "Worker config missing: $configFile"
}

$values = @{}
Get-Content -LiteralPath $configFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $name,$value = $line.Split('=',2)
        $values[$name.Trim()] = $value.Trim()
    }
}

$required = @('DATABASE_URL','ADMIN_ID','POCKET_OPTION_SSID','WORKER_SHARED_SECRET')
$missing = @($required | Where-Object { -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) })
if ($missing.Count -gt 0) {
    throw ('Worker config has empty required fields: ' + ($missing -join ', '))
}
if ([string]$values['POCKET_OPTION_DEMO'] -notmatch '^(?i:true|1|yes|on)$') {
    throw 'POCKET_OPTION_DEMO must be true. REAL AUTO is not allowed.'
}
if ([string]$values['WORKER_SHARED_SECRET'] -and ([string]$values['WORKER_SHARED_SECRET']).Length -lt 32) {
    throw 'WORKER_SHARED_SECRET must contain at least 32 characters.'
}
if ([string]$values['POCKET_OPTION_SSID'] -and ([string]$values['POCKET_OPTION_SSID']).Length -lt 10) {
    throw 'POCKET_OPTION_SSID looks incomplete.'
}
try {
    if ([int64]$values['ADMIN_ID'] -le 0) { throw 'invalid' }
} catch {
    throw 'ADMIN_ID must be a positive integer.'
}

Set-Location -LiteralPath $repoRoot
& $python -m compileall -q backend worker api bot
if ($LASTEXITCODE -ne 0) { throw 'Python compile validation failed.' }
& $python -c "from backend.services.auto_realtime import realtime_driver_enabled; from backend.services.pocketoption_otc import PocketOptionOTCService; print('IMPORTS_OK')"
if ($LASTEXITCODE -ne 0) { throw 'Worker import validation failed.' }

$branch = (& git branch --show-current 2>$null).Trim()
if ($branch) {
    $expected = [string]$values['WORKER_UPDATE_BRANCH']
    if ($expected -and $branch -ne $expected) {
        throw "Git branch mismatch. Current=$branch, WORKER_UPDATE_BRANCH=$expected"
    }
}

Write-Host 'PREFLIGHT_OK'
Write-Host "Repo: $repoRoot"
Write-Host "Config: required values present (values hidden)"
Write-Host 'Mode: DEMO-only'
Write-Host 'Python: imports and compile OK'
if ($branch) { Write-Host "Branch: $branch" }
