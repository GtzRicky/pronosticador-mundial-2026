param(
    [string]$TaskName = "Quiniela Mundial 2026 Matchday",
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
$Runner = Join-Path $Root "scripts\15_run_matchday.py"
$Arguments = "`"$Runner`""
$EnvPath = Join-Path $Root ".env"

if (-not (Test-Path $Runner)) {
    throw "No se encontro el runner: $Runner"
}

if (-not (Test-Path $EnvPath)) {
    Write-Warning "No se encontro .env en $Root. Copia .env.example a .env y configura API_FOOTBALL_KEY/ntfy antes de depender de la tarea."
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
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
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
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Actualiza fixtures, alineaciones, odds, resultados, predicciones, HTML y notificaciones del Mundial 2026." `
    -Force | Out-Null

Write-Host "Tarea instalada: $TaskName"
Write-Host "Python: $Python ($PythonSource)"
