param(
    [string]$TaskName = "Quiniela Mundial 2026 Planner",
    [string]$DailyAt = "00:10",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Resolve-Path (Join-Path $Root ".venv\Scripts\python.exe")).Path
$Pythonw = (Resolve-Path (Join-Path $Root ".venv\Scripts\pythonw.exe")).Path
$Bootstrap = Join-Path $Root "scripts\run_scheduled_python.py"
$Planner = Join-Path $Root "scripts\32_plan_wake_tasks.py"
$ErrorLog = Join-Path $Root "outputs\logs\planner_scheduler_errors.log"
$Arguments = "`"$Bootstrap`" --script `"$Planner`" --working-directory `"$Root`" --error-log `"$ErrorLog`""
$DailyTime = [DateTime]::Today.Add([TimeSpan]::Parse($DailyAt))

if ($DryRun) {
    [pscustomobject]@{
        TaskName = $TaskName
        Execute = $Pythonw
        Arguments = $Arguments
        DailyAt = $DailyTime
        AtLogOn = $true
        WakeToRun = $true
        StartWhenAvailable = $true
        OneShotPrefix = "Quiniela Mundial 2026 Wake "
        HorizonDays = 3
        PreMatchWindows = "T-60,T-30,T-15,T-5,T-1"
        PostMatchPolling = "T+105..T+360 cada 15 minutos"
    } | Format-List
    exit 0
}

$LegacyTasks = Get-ScheduledTask `
    -TaskName "Quiniela Mundial 2026 Matchday","Quiniela Mundial 2026 Notifications" `
    -ErrorAction SilentlyContinue
$LegacyTasks | Stop-ScheduledTask -ErrorAction SilentlyContinue
$LegacyTasks | Unregister-ScheduledTask -Confirm:$false

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument $Arguments `
    -WorkingDirectory $Root
$Triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $DailyTime),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)
$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Planifica tareas WakeToRun puntuales para ventanas y sondeos del Mundial 2026." `
    -Force | Out-Null

powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 | Out-Null
powercfg /setactive SCHEME_CURRENT | Out-Null

& $Python $Planner
if ($LASTEXITCODE -ne 0) {
    throw "El planificador inicial fallo con codigo $LASTEXITCODE"
}

Write-Host "Tarea instalada: $TaskName"
Write-Host "Planificacion diaria: $DailyAt y al iniciar sesion"
Write-Host "Polling cada minuto eliminado."
