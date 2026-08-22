[CmdletBinding()]
param([string]$TaskName = 'AlphaPulse Worker')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$statusScript = Join-Path $PSScriptRoot 'status.ps1'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$git = (Get-Command git.exe -ErrorAction Stop).Source

$statusOutput = & $statusScript | Select-Object -Last 1
$status = $statusOutput | ConvertFrom-Json
if (-not $status.safe_to_update) {
    throw "Update blocked: active_sessions=$($status.active_sessions), unresolved_positions=$($status.unresolved_positions)"
}

Set-Location -LiteralPath $repoRoot
if (& $git status --porcelain) { throw 'Update blocked: the local repository has uncommitted changes.' }
& $git fetch origin main
& $git merge --ff-only origin/main
& $python -m pip install --disable-pip-version-check -r requirements.txt
Push-Location -LiteralPath (Join-Path $repoRoot 'miniapp')
try {
    & $npm ci
    & $npm run build
}
finally { Pop-Location }
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Write-Host 'AlphaPulse updated and worker restarted.'
