[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command py.exe -ErrorAction Stop }

Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $pythonCommand.Source -m venv .venv
}
$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $python -m pip install --disable-pip-version-check -r requirements.txt
& $python -m compileall -q backend worker api bot
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Worker bootstrap tests failed with exit code $LASTEXITCODE" }
Write-Host 'AlphaPulse Windows worker dependencies and tests are ready.'
Write-Host 'Node.js is not required on the trading worker PC.'
