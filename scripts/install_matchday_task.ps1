param([switch]$DryRun)

& (Join-Path $PSScriptRoot "install_wake_scheduler.ps1") -DryRun:$DryRun
exit $LASTEXITCODE
