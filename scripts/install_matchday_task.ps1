param(
    [string]$TaskName = "Quiniela Mundial 2026 Matchday",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python).Source
$Runner = Join-Path $Root "scripts\15_run_matchday.py"
$Arguments = "`"$Runner`""

if ($DryRun) {
    [pscustomobject]@{
        TaskName = $TaskName
        Execute = $Python
        Arguments = $Arguments
        WorkingDirectory = $Root
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
