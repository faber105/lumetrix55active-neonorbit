[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$configFile = Join-Path $baseRoot 'config\worker.env'
$logRoot = Join-Path $baseRoot 'logs'
$runtimeRoot = Join-Path $baseRoot 'runtime'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) { throw "Python venv missing: $python" }
if (-not (Test-Path -LiteralPath $configFile)) { throw "Worker configuration missing: $configFile" }
New-Item -ItemType Directory -Force -Path $logRoot,$runtimeRoot | Out-Null

Get-Content -LiteralPath $configFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $name,$value = $line.Split('=',2)
        [Environment]::SetEnvironmentVariable($name.Trim(),$value.Trim(),'Process')
    }
}
$env:APP_RUNTIME_ROLE = 'worker'
$env:AUTO_REALTIME_DRIVER = 'true'
$env:POCKET_OPTION_DEMO = 'true'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONPATH = $repoRoot

$mutex = [Threading.Mutex]::new($false,'AlphaPulseWorkerSingleInstance')
if (-not $mutex.WaitOne(0)) { throw 'Another AlphaPulse worker instance is already running' }
try {
    Get-ChildItem -LiteralPath $logRoot -Filter 'worker-*.log' -File |
        Where-Object LastWriteTime -lt (Get-Date).AddDays(-14) |
        Remove-Item -Force
    $logFile = Join-Path $logRoot ("worker-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
    Set-Location -LiteralPath $repoRoot

    # Windows PowerShell turns native stderr into ErrorRecord objects and, with
    # ErrorActionPreference=Stop, can terminate the script even for normal
    # Python logging. Run through cmd.exe so stdout/stderr are appended to the
    # worker log as plain text and preserve the real Python exit code.
    $quotedPython = '"' + $python + '"'
    $quotedLog = '"' + $logFile + '"'
    $commandLine = "$quotedPython -m worker.main >> $quotedLog 2>&1"
    & $env:ComSpec /d /s /c $commandLine
    $exitCode = $LASTEXITCODE
    exit $exitCode
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
