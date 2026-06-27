$ErrorActionPreference = "Stop"

Get-ScheduledTask -TaskName "Quiniela Mundial 2026 Planner" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "Quiniela Mundial 2026 Wake *" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "Quiniela Mundial 2026 Matchday" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "Quiniela Mundial 2026 Notifications" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

Write-Host "Automatizacion Quiniela eliminada."
