param(
    [string]$TaskName = "Quiniela Mundial 2026 Notifications",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = (Resolve-Path $VenvPython).Path
    $PythonSource = "repo .venv"
} else {
    $PythonCommand = Get-Command python -ErrorAction Stop
    $Python = $PythonCommand.Source
    $PythonSource = "PATH"
}
$Runner = Join-Path $Root "scripts\27_run_notification_cycle.py"
$Arguments = "`"$Runner`""
$EnvPath = Join-Path $Root ".env"

if (-not (Test-Path $Runner)) {
    throw "No se encontro el runner: $Runner"
}

if (-not (Test-Path $EnvPath)) {
    Write-Warning "No se encontro .env en $Root. Configura ntfy antes de depender de la tarea."
}

if ($DryRun) {
    [pscustomobject]@{
        TaskName = $TaskName
        Execute = $Python
        PythonSource = $PythonSource
        Arguments = $Arguments
        WorkingDirectory = $Root
        EnvFileExists = Test-Path $EnvPath
        IntervalMinutes = 1
        WakeToRun = $true
        StartWhenAvailable = $true
        MultipleInstances = "IgnoreNew"
        ExecutionTimeLimitMinutes = 3
    } | Format-List
    exit 0
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument $Arguments `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Despacha notificaciones pendientes y programa ventanas T-15/T-5 sin depender del refresh pesado." `
    -Force | Out-Null

Write-Host "Tarea instalada: $TaskName"
Write-Host "Python: $Python ($PythonSource)"
