param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$PidFile = Join-Path $RuntimeDir "server.pid"
$StdoutLog = Join-Path $RuntimeDir "server.stdout.log"
$StderrLog = Join-Path $RuntimeDir "server.stderr.log"
$HealthUrl = "http://127.0.0.1:8000/api/health"
$AppUrl = "http://localhost:8000/"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment is missing. Expected: $Python"
}

# Some Windows environments expose both Path and PATH. Start-Process treats
# those as duplicate dictionary keys, so rebuild one canonical process path.
$cleanPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $cleanPath = "$cleanPath;$userPath"
}
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $cleanPath

if (Test-Path -LiteralPath $PidFile) {
    $previousPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if ($previousPid -match '^\d+$') {
        Stop-Process -Id ([int]$previousPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$existingListener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existingListener) {
    throw "Port 8000 is already used by process $($existingListener.OwningProcess). Stop that process and try again."
}

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList "-m", "app" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($process.HasExited) {
        break
    }
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 1
        if ($health.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    $details = if (Test-Path -LiteralPath $StderrLog) {
        (Get-Content -LiteralPath $StderrLog -Tail 20) -join [Environment]::NewLine
    } else {
        "No server error log was produced."
    }
    throw "Mongo Production System did not start.`n`n$details"
}

if (-not $SkipBrowser) {
    Start-Process $AppUrl
}
