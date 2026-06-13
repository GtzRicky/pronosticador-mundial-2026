param(
    [string]$TaskName = "Quiniela Mundial 2026 Matchday",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if ($DryRun) {
    Write-Host "Se eliminaría la tarea: $TaskName"
    exit 0
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-Host "La tarea no existe: $TaskName"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Tarea eliminada: $TaskName"
