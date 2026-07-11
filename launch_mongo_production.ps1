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

# Uvicorn can create a worker process on Windows, leaving the PID file pointing
# at its venv parent. netstat reliably exposes the actual port owner here.
$processIds = [System.Collections.Generic.HashSet[int]]::new()
if (Test-Path -LiteralPath $PidFile) {
    $previousPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    if ($previousPid -match '^\d+$') {
        [void]$processIds.Add([int]$previousPid)
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

& netstat.exe -ano -p tcp |
    Select-String -Pattern '^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$' |
    ForEach-Object {
        [void]$processIds.Add([int]$_.Matches[0].Groups[1].Value)
    }

foreach ($processId in $processIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
if ($processIds.Count -gt 0) {
    Start-Sleep -Milliseconds 500
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
