[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Find-StablePython {
    $candidates = @()
    try {
        $candidates += Get-Command python.exe -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    } catch {}
    try {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $pyPaths = & $launcher.Source -0p 2>$null
            if ($LASTEXITCODE -eq 0) {
                $candidates += $pyPaths | ForEach-Object { ($_ -replace '^\s*-V:\S+\s+', '').Trim() }
            }
        }
    } catch {}

    $candidates = $candidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -Unique

    $stable = $candidates | Where-Object { $_ -notmatch '[\\/]\.cache[\\/]codex-runtimes[\\/]' } | Select-Object -First 1
    if ($stable) { return $stable }

    $fallback = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path -LiteralPath $fallback) { return $fallback }

    throw 'No stable Python installation found outside the Codex runtime cache.'
}

$basePython = Find-StablePython
Write-Host "Using base Python: $basePython"

Set-Location -LiteralPath $repoRoot
$venvRoot = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$venvConfig = Join-Path $venvRoot 'pyvenv.cfg'
$venvHealthy = $false
$venvUsesCodexRuntime = $false
if (Test-Path -LiteralPath $venvConfig) {
    try {
        $venvUsesCodexRuntime = (Get-Content -LiteralPath $venvConfig -Raw) -match '[\\/]\.cache[\\/]codex-runtimes[\\/]'
    } catch {}
}
if ((Test-Path -LiteralPath $venvPython) -and -not $venvUsesCodexRuntime) {
    try {
        & $venvPython -c "import sys; print(sys.executable)" *> $null
        $venvHealthy = ($LASTEXITCODE -eq 0)
    } catch {
        $venvHealthy = $false
    }
}

if (-not $venvHealthy) {
    if (Test-Path -LiteralPath $venvRoot) {
        Write-Host 'Rebuilding .venv with a stable local Python installation.'
        Remove-Item -LiteralPath $venvRoot -Recurse -Force
    }
    & $basePython -m venv .venv
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
