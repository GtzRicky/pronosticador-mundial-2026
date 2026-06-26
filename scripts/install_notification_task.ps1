param([switch]$DryRun)

& (Join-Path $PSScriptRoot "install_notification_watchdog_task.ps1") -DryRun:$DryRun
exit $LASTEXITCODE
