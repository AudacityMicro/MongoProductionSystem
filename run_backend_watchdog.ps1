param(
    [ValidateRange(10, 300)]
    [int]$CheckIntervalSeconds = 30,
    [ValidateRange(5, 300)]
    [int]$RestartDelaySeconds = 15
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$HealthUrl = "http://127.0.0.1:8000/api/health"
$Launcher = Join-Path $ProjectRoot "launch_mongo_production.ps1"
$LogPath = Join-Path $RuntimeDir "backend-watchdog.log"
$MaximumLogBytes = 5MB

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Write-WatchdogLog([string]$Message) {
    if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -ge $MaximumLogBytes) {
        $BackupPath = "$LogPath.1"
        Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $LogPath -Destination $BackupPath -Force
    }
    "$(Get-Date -Format o) $Message" | Add-Content -LiteralPath $LogPath
}

function Test-BackendHealth {
    try {
        $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
        return $Health.status -eq "ok" -and $Health.database -eq "ok"
    } catch {
        return $false
    }
}

Write-WatchdogLog "Backend watchdog started."
while ($true) {
    if (Test-BackendHealth) {
        Start-Sleep -Seconds $CheckIntervalSeconds
        continue
    }

    # The launcher refuses to replace a reachable backend that cannot prepare
    # for shutdown. This watchdog therefore only recovers a dead process; it
    # never force-kills an active or uncertain production workflow.
    Write-WatchdogLog "Health check failed; requesting a safe backend launch."
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher -SkipBrowser
        if ($LASTEXITCODE -ne 0) {
            throw "Launcher exited with code $LASTEXITCODE."
        }
        if (Test-BackendHealth) {
            Write-WatchdogLog "Backend health restored."
        } else {
            Write-WatchdogLog "Launcher returned, but backend health is still unavailable."
        }
    } catch {
        Write-WatchdogLog "Safe launch was refused or failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $RestartDelaySeconds
}
