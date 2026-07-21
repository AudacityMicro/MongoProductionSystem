param(
    [string]$TaskName = "Mongo Production System Watchdog"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Watchdog = Join-Path $ProjectRoot "run_backend_watchdog.ps1"

if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "Backend watchdog script was not found: $Watchdog"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Watchdog`""
)
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Safely restarts the Mongo Production System backend only when its local health endpoint is unavailable." `
    -Force | Out-Null

Write-Output "Installed scheduled task: $TaskName"
