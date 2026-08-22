[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command py.exe -ErrorAction Stop }

Set-Location -LiteralPath $repoRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$venvHealthy = $false
if (Test-Path -LiteralPath $venvPython) {
    try {
        & $venvPython -c "import sys; print(sys.executable)" *> $null
        $venvHealthy = ($LASTEXITCODE -eq 0)
    } catch {
        $venvHealthy = $false
    }
}

if (-not $venvHealthy) {
    if (Test-Path -LiteralPath (Join-Path $repoRoot '.venv')) {
        Write-Host 'Existing .venv is broken or still points to the old Codex runtime; rebuilding it locally.'
        Remove-Item -LiteralPath (Join-Path $repoRoot '.venv') -Recurse -Force
    }
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed with exit code $LASTEXITCODE" }
}

$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $python -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed with exit code $LASTEXITCODE" }
& $python -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Worker dependency installation failed with exit code $LASTEXITCODE" }
& $python -m pip install --disable-pip-version-check "pytest>=8,<9"
if ($LASTEXITCODE -ne 0) { throw "pytest installation failed with exit code $LASTEXITCODE" }
& $python -m compileall -q backend worker api bot
if ($LASTEXITCODE -ne 0) { throw "Worker compile validation failed with exit code $LASTEXITCODE" }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Worker bootstrap tests failed with exit code $LASTEXITCODE" }
Write-Host 'AlphaPulse Windows worker dependencies and tests are ready.'
Write-Host 'Node.js is not required on the trading worker PC.'
