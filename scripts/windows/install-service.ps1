[CmdletBinding()]
param(
    [string]$ServiceUser = 'AlphaPulseWorker',
    [string]$TaskName = 'AlphaPulse Worker'
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from PowerShell as Administrator.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseRoot = Split-Path $repoRoot -Parent
$configRoot = Join-Path $baseRoot 'config'
$configFile = Join-Path $configRoot 'worker.env'
$logRoot = Join-Path $baseRoot 'logs'
$runtimeRoot = Join-Path $baseRoot 'runtime'
$runner = Join-Path $PSScriptRoot 'run-worker.ps1'
New-Item -ItemType Directory -Force -Path $configRoot,$logRoot,$runtimeRoot | Out-Null

# Generate a fresh high-entropy password on every registration. The password is
# intentionally not persisted anywhere; rerunning this installer rotates it and
# updates the scheduled task credentials atomically.
$passwordBytes = [byte[]]::new(36)
[Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)
$passwordText = [Convert]::ToBase64String($passwordBytes) + 'Aa1!'
$securePassword = ConvertTo-SecureString $passwordText -AsPlainText -Force
$existingUser = Get-LocalUser -Name $ServiceUser -ErrorAction SilentlyContinue
if (-not $existingUser) {
    New-LocalUser -Name $ServiceUser -Password $securePassword -AccountNeverExpires -UserMayNotChangePassword | Out-Null
} else {
    Set-LocalUser -Name $ServiceUser -Password $securePassword -AccountNeverExpires $true
}

if (-not (Test-Path -LiteralPath $configFile)) {
    @(
        'DATABASE_URL=',
        'ADMIN_ID=',
        'TELEGRAM_BOT_TOKEN=',
        'POCKET_OPTION_SSID=',
        'POCKET_OPTION_DEMO=true',
        'WORKER_SHARED_SECRET=',
        'REALTIME_TRANSPORT=polling',
        'WORKER_HTTP_PORT=8765',
        'WORKER_UPDATE_BRANCH=codex-windows-worker-rebuild',
        'LOG_LEVEL=INFO'
    ) | Set-Content -LiteralPath $configFile -Encoding UTF8
}

icacls $repoRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)RX" 'SYSTEM:(OI)(CI)F' | Out-Null
icacls $configRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)R" 'SYSTEM:(OI)(CI)F' | Out-Null
icacls $logRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)M" 'SYSTEM:(OI)(CI)F' | Out-Null
icacls $runtimeRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)M" 'SYSTEM:(OI)(CI)F' | Out-Null

$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId ".\$ServiceUser" -LogonType Password -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Description 'AlphaPulse DEMO-only Windows worker'
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -InputObject $task -User ".\$ServiceUser" -Password $passwordText -Force | Out-Null

powercfg /change standby-timeout-ac 0 | Out-Null
Write-Host "Installed/refreshed. Fill $configFile, then run: Start-ScheduledTask -TaskName '$TaskName'"
