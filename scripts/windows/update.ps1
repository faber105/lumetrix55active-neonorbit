[CmdletBinding()]
param(
    [string]$TaskName = 'AlphaPulse Worker',
    [string]$Branch = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$statusScript = Join-Path $PSScriptRoot 'status.ps1'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$git = (Get-Command git.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv missing: $python"
}

$statusOutput = & $statusScript | Select-Object -Last 1
$status = $statusOutput | ConvertFrom-Json
if (-not $status.safe_to_update) {
    throw "Update blocked: active_sessions=$($status.active_sessions), unresolved_positions=$($status.unresolved_positions), unresolved_executions=$($status.unresolved_executions)"
}

Set-Location -LiteralPath $repoRoot
if (& $git status --porcelain) {
    throw 'Update blocked: the local repository has uncommitted changes.'
}

$currentBranch = (& $git branch --show-current).Trim()
$targetBranch = if (-not [string]::IsNullOrWhiteSpace($Branch)) {
    $Branch.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace($env:WORKER_UPDATE_BRANCH)) {
    $env:WORKER_UPDATE_BRANCH.Trim()
} else {
    $currentBranch
}
if ([string]::IsNullOrWhiteSpace($targetBranch)) {
    throw 'Update blocked: detached HEAD. Supply -Branch explicitly.'
}
if ($currentBranch -ne $targetBranch) {
    throw "Update blocked: current branch '$currentBranch' differs from target '$targetBranch'. Checkout the target branch first."
}

# A worker host only needs the Python runtime. Building the Telegram Mini App on
# the trading PC would unnecessarily require Node/npm and couples worker updates
# to frontend tooling. The public web deployment remains responsible for UI builds.
& $git fetch origin $targetBranch
& $git merge --ff-only "origin/$targetBranch"
& $python -m pip install --disable-pip-version-check -r requirements.txt
& $python -m compileall -q backend worker
if ($LASTEXITCODE -ne 0) {
    throw 'Python compile validation failed; worker was not restarted.'
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Write-Host "AlphaPulse worker updated from '$targetBranch' and restarted."
