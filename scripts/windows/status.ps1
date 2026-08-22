[CmdletBinding()]
param([string]$TaskName = 'AlphaPulse Worker')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$configFile = Join-Path $baseRoot 'config\worker.env'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $configFile) {
    Get-Content -LiteralPath $configFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $name,$value = $line.Split('=',2)
            [Environment]::SetEnvironmentVariable($name.Trim(),$value.Trim(),'Process')
        }
    }
}
$env:PYTHONPATH = $repoRoot
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object TaskName,LastRunTime,LastTaskResult,NextRunTime }
if (Test-Path -LiteralPath $python) {
    Set-Location -LiteralPath $repoRoot
    & $python -m worker.status
}
