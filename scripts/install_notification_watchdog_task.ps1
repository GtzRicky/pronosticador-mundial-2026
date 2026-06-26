param(
    [string]$TaskName = "Quiniela Mundial 2026 Notification Watchdog",
    [string]$EveryMinutes = "2",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Pythonw = (Resolve-Path (Join-Path $Root ".venv\Scripts\pythonw.exe")).Path
$Bootstrap = Join-Path $Root "scripts\run_scheduled_python.py"
$Watchdog = Join-Path $Root "scripts\33_run_notification_watchdog.py"
$ErrorLog = Join-Path $Root "outputs\logs\notification_watchdog_errors.log"
$LockDirectory = Join-Path $env:TEMP "quiniela_notification_watchdog"
$LockFile = Join-Path $LockDirectory "notification_watchdog.lock"
$Arguments = "`"$Bootstrap`" --script `"$Watchdog`" --working-directory `"$Root`" --error-log `"$ErrorLog`" --lock-file `"$LockFile`" --lock-timeout-seconds 0 --skip-if-lock-busy"
$StartAt = [DateTime]::Today
$Interval = New-TimeSpan -Minutes ([int]$EveryMinutes)

if ($DryRun) {
    [pscustomobject]@{
        TaskName = $TaskName
        Execute = $Pythonw
        Arguments = $Arguments
        StartAt = $StartAt
        EveryMinutes = [int]$EveryMinutes
        WakeToRun = $true
        StartWhenAvailable = $true
        MultipleInstances = "IgnoreNew"
        ExecutionTimeLimitMinutes = 3
    } | Format-List
    exit 0
}

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument $Arguments `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At $StartAt `
    -RepetitionInterval $Interval `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Despacha el outbox de notificaciones Quiniela cada $EveryMinutes minutos sin ejecutar refresh pesado." `
    -Force | Out-Null

Write-Host "Tarea instalada: $TaskName"
Write-Host "Cadencia: cada $EveryMinutes minutos"
