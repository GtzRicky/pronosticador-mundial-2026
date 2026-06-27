param(
    [Parameter(Mandatory = $true)]
    [string]$SchedulePath
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Schedule = Get-Content -LiteralPath $SchedulePath -Raw | ConvertFrom-Json
$TaskPrefix = "Quiniela Mundial 2026 Wake "
$Pythonw = (Resolve-Path (Join-Path $Root ".venv\Scripts\pythonw.exe")).Path
$Bootstrap = Join-Path $Root "scripts\run_scheduled_python.py"
$Runner = Join-Path $Root "scripts\15_run_matchday.py"
$ErrorLog = Join-Path $Root "outputs\logs\matchday_scheduler_errors.log"
$Arguments = "`"$Bootstrap`" --script `"$Runner`" --working-directory `"$Root`" --error-log `"$ErrorLog`" --lock-timeout-seconds 1 --skip-if-lock-busy"

Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 60)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument $Arguments `
    -WorkingDirectory $Root

foreach ($Event in $Schedule.events) {
    $At = [DateTimeOffset]::Parse([string]$Event.scheduled_for).LocalDateTime
    $Trigger = New-ScheduledTaskTrigger -Once -At $At
    $ReasonText = ($Event.reasons -join ", ")
    Register-ScheduledTask `
        -TaskName ([string]$Event.task_name) `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Wake puntual Quiniela Mundial 2026: $ReasonText" `
        -Force | Out-Null
}

Write-Output "Wake tasks sincronizadas: $($Schedule.events.Count)"
