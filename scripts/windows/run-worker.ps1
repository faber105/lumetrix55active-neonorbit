[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$configFile = Join-Path $baseRoot 'config\worker.env'
$logRoot = Join-Path $baseRoot 'logs'
$runtimeRoot = Join-Path $baseRoot 'runtime'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$launcherLog = Join-Path $logRoot 'start-worker-last.log'

New-Item -ItemType Directory -Force -Path $logRoot,$runtimeRoot | Out-Null

function Write-LauncherLog([string]$message) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    Add-Content -LiteralPath $launcherLog -Value ("$stamp $message") -Encoding UTF8
}

try {
    Set-Content -LiteralPath $launcherLog -Value '' -Encoding UTF8
    Write-LauncherLog "launcher-start user=$([Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    Write-LauncherLog "repo=$repoRoot"

    if (-not (Test-Path -LiteralPath $python)) { throw "Python venv missing: $python" }
    if (-not (Test-Path -LiteralPath $configFile)) { throw "Worker configuration missing: $configFile" }
    Write-LauncherLog 'paths-ok'

    Get-Content -LiteralPath $configFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $name,$value = $line.Split('=',2)
            [Environment]::SetEnvironmentVariable($name.Trim(),$value.Trim(),'Process')
        }
    }
    Write-LauncherLog 'config-loaded'

    $env:APP_RUNTIME_ROLE = 'worker'
    $env:AUTO_REALTIME_DRIVER = 'true'
    $env:POCKET_OPTION_DEMO = 'true'
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONPATH = $repoRoot

    $mutex = [Threading.Mutex]::new($false,'AlphaPulseWorkerSingleInstance')
    $ownsMutex = $false
    try {
        $ownsMutex = $mutex.WaitOne(0)
        if (-not $ownsMutex) { throw 'Another AlphaPulse worker instance is already running' }
        Write-LauncherLog 'mutex-acquired'

        Get-ChildItem -LiteralPath $logRoot -Filter 'worker-*.log' -File |
            Where-Object LastWriteTime -lt (Get-Date).AddDays(-14) |
            Remove-Item -Force
        $logFile = Join-Path $logRoot ("worker-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
        Set-Location -LiteralPath $repoRoot
        Write-LauncherLog "python-launch log=$logFile"

        $quotedPython = '"' + $python + '"'
        $quotedLog = '"' + $logFile + '"'
        $commandLine = "$quotedPython -m worker.main >> $quotedLog 2>&1"
        & $env:ComSpec /d /s /c $commandLine
        $exitCode = $LASTEXITCODE
        Write-LauncherLog "python-exit code=$exitCode"
        exit $exitCode
    }
    finally {
        if ($ownsMutex) {
            try { $mutex.ReleaseMutex() } catch {}
        }
        $mutex.Dispose()
    }
}
catch {
    try {
        Write-LauncherLog ("launcher-error type={0} message={1}" -f $_.Exception.GetType().FullName, $_.Exception.Message)
        Write-LauncherLog ("launcher-error-position {0}" -f $_.InvocationInfo.PositionMessage.Replace("`r",' ').Replace("`n",' '))
    } catch {}
    exit 1
}
