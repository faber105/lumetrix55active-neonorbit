[CmdletBinding()]
param(
    [string]$ServiceUser = 'AlphaPulseWorker',
    [string]$TaskName = 'AlphaPulse Worker',
    [switch]$RemoveServiceUser
)

$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if ($RemoveServiceUser) {
    Remove-LocalUser -Name $ServiceUser -ErrorAction SilentlyContinue
}
Write-Host 'Scheduled task removed. Configuration and logs were preserved.'
