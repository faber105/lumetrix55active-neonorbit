[CmdletBinding()]
param([string]$TargetRoot = 'C:\AlphaPulse')

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from PowerShell as Administrator.'
}

$sourceApp = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceRoot = Split-Path $sourceApp -Parent
$resolvedTarget = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
if ($resolvedTarget -ne 'C:\AlphaPulse') {
    throw "Safety check refused unexpected target: $resolvedTarget"
}
$targetApp = Join-Path $resolvedTarget 'app'
if (Test-Path -LiteralPath $targetApp) {
    $existing = Get-ChildItem -LiteralPath $targetApp -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing) { throw "$targetApp is not empty; no files were overwritten." }
}

New-Item -ItemType Directory -Force -Path $targetApp,(Join-Path $resolvedTarget 'config'),(Join-Path $resolvedTarget 'logs'),(Join-Path $resolvedTarget 'runtime'),(Join-Path $resolvedTarget 'backups') | Out-Null
& robocopy.exe $sourceApp $targetApp /E /COPY:DAT /R:2 /W:1 /XD '.venv' 'node_modules' 'dist' '__pycache__' | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit code $LASTEXITCODE" }
$sourceConfig = Join-Path $sourceRoot 'config\worker.env'
if (Test-Path -LiteralPath $sourceConfig) {
    Copy-Item -LiteralPath $sourceConfig -Destination (Join-Path $resolvedTarget 'config\worker.env')
}

Write-Host "Copied AlphaPulse to $targetApp without deleting the source."
Write-Host "Next run $targetApp\scripts\windows\bootstrap.ps1, then install-service.ps1."
