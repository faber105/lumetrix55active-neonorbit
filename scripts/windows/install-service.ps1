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

$passwordBytes = [byte[]]::new(36)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($passwordBytes)
} finally {
    $rng.Dispose()
}
$passwordText = [Convert]::ToBase64String($passwordBytes) + 'Aa1!'
$securePassword = ConvertTo-SecureString $passwordText -AsPlainText -Force
$existingUser = Get-LocalUser -Name $ServiceUser -ErrorAction SilentlyContinue
if (-not $existingUser) {
    New-LocalUser -Name $ServiceUser -Password $securePassword -AccountNeverExpires -UserMayNotChangePassword | Out-Null
} else {
    Set-LocalUser -Name $ServiceUser -Password $securePassword -AccountNeverExpires
}
$localUser = Get-LocalUser -Name $ServiceUser -ErrorAction Stop
$serviceAccount = "$env:COMPUTERNAME\$ServiceUser"
$serviceSid = $localUser.SID.Value

if (-not ('AlphaPulse.LsaRights' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace AlphaPulse {
    public static class LsaRights {
        [StructLayout(LayoutKind.Sequential)]
        private struct LSA_OBJECT_ATTRIBUTES {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public uint Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct LSA_UNICODE_STRING {
            public ushort Length;
            public ushort MaximumLength;
            public IntPtr Buffer;
        }

        [DllImport("advapi32.dll")]
        private static extern uint LsaOpenPolicy(
            IntPtr SystemName,
            ref LSA_OBJECT_ATTRIBUTES ObjectAttributes,
            uint DesiredAccess,
            out IntPtr PolicyHandle);

        [DllImport("advapi32.dll")]
        private static extern uint LsaAddAccountRights(
            IntPtr PolicyHandle,
            byte[] AccountSid,
            LSA_UNICODE_STRING[] UserRights,
            uint CountOfRights);

        [DllImport("advapi32.dll")]
        private static extern uint LsaClose(IntPtr PolicyHandle);

        [DllImport("advapi32.dll")]
        private static extern uint LsaNtStatusToWinError(uint Status);

        public static void Grant(string sidText, string rightName) {
            const uint POLICY_CREATE_ACCOUNT = 0x00000010;
            const uint POLICY_LOOKUP_NAMES = 0x00000800;
            var attrs = new LSA_OBJECT_ATTRIBUTES();
            attrs.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
            IntPtr policy;
            uint status = LsaOpenPolicy(IntPtr.Zero, ref attrs, POLICY_CREATE_ACCOUNT | POLICY_LOOKUP_NAMES, out policy);
            if (status != 0) {
                throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
            }

            IntPtr buffer = IntPtr.Zero;
            try {
                var sid = new SecurityIdentifier(sidText);
                var sidBytes = new byte[sid.BinaryLength];
                sid.GetBinaryForm(sidBytes, 0);

                buffer = Marshal.StringToHGlobalUni(rightName);
                var right = new LSA_UNICODE_STRING {
                    Buffer = buffer,
                    Length = (ushort)(rightName.Length * 2),
                    MaximumLength = (ushort)((rightName.Length + 1) * 2)
                };
                status = LsaAddAccountRights(policy, sidBytes, new[] { right }, 1);
                if (status != 0) {
                    throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
                }
            } finally {
                if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
                LsaClose(policy);
            }
        }
    }
}
'@
}
[AlphaPulse.LsaRights]::Grant($serviceSid, 'SeBatchLogonRight')

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
icacls $configFile /inheritance:r /grant:r "${env:USERNAME}:F" "${ServiceUser}:R" 'SYSTEM:F' | Out-Null
icacls $logRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)M" 'SYSTEM:(OI)(CI)F' | Out-Null
icacls $runtimeRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "${ServiceUser}:(OI)(CI)M" 'SYSTEM:(OI)(CI)F' | Out-Null

# A venv created from a per-user Python installation still launches the base
# interpreter from pyvenv.cfg. The dedicated worker account therefore needs
# read/execute access to that stable Python directory as well.
$pyvenvConfig = Join-Path $repoRoot '.venv\pyvenv.cfg'
if (Test-Path -LiteralPath $pyvenvConfig) {
    $homeLine = Get-Content -LiteralPath $pyvenvConfig | Where-Object { $_ -match '^home\s*=\s*' } | Select-Object -First 1
    if ($homeLine) {
        $pythonHome = ($homeLine -replace '^home\s*=\s*','').Trim()
        if ($pythonHome -and (Test-Path -LiteralPath $pythonHome)) {
            icacls $pythonHome /grant "${ServiceUser}:(OI)(CI)RX" /T /C | Out-Null
            Write-Host "Worker read/execute access granted to base Python: $pythonHome"
        }
    }
}

$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $serviceSid -LogonType Password -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Description 'AlphaPulse DEMO-only Windows worker'
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -InputObject $task -User $serviceAccount -Password $passwordText -Force | Out-Null

powercfg /change standby-timeout-ac 0 | Out-Null
Write-Host "Installed/refreshed for $serviceAccount. Batch logon right granted. Fill $configFile, then run: Start-ScheduledTask -TaskName '$TaskName'"
