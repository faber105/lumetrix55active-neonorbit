[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command py.exe -ErrorAction Stop }
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source

Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $pythonCommand.Source -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements.txt
Push-Location -LiteralPath (Join-Path $repoRoot 'miniapp')
try {
    & $npm ci
    & $npm run build
}
finally { Pop-Location }
& '.\.venv\Scripts\python.exe' -m pytest -q
Write-Host 'AlphaPulse dependencies, build and tests are ready.'
