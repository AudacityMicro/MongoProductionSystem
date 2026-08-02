param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectRoot "launch_mongo_production.ps1"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogPath = Join-Path $RuntimeDir "kiosk-startup.log"
$HealthUrl = "http://127.0.0.1:8000/api/health"
$DashboardUrl = "http://127.0.0.1:8000/dashboard"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Write-StartupLog([string]$Message) {
    "$(Get-Date -Format o) $Message" | Add-Content -LiteralPath $LogPath
}

function Test-BackendHealth {
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3
        return $health.status -eq "ok" -and $health.database -eq "ok"
    } catch {
        return $false
    }
}

try {
    if (-not (Test-BackendHealth)) {
        Write-StartupLog "Backend unavailable; starting MPS launcher."
        & $Launcher -SkipBrowser
        if (-not $?) {
            throw "MPS launcher failed."
        }
    } else {
        Write-StartupLog "Backend already healthy; leaving it running."
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-BackendHealth) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        throw "MPS health endpoint did not become ready."
    }

    if (-not $SkipBrowser) {
        $chromeCandidates = @(
            "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
        )
        $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $chrome) {
            throw "Google Chrome was not found."
        }

        Start-Process -FilePath $chrome -ArgumentList @(
            "--kiosk",
            "--no-first-run",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--app=$DashboardUrl"
        )
        Write-StartupLog "Chrome kiosk opened at $DashboardUrl."
    }
} catch {
    Write-StartupLog "Startup failed: $($_.Exception.Message)"
    exit 1
}
