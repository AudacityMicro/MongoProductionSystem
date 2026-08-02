param(
    [string]$RemoteName = "origin",
    [string]$Branch = "main",
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Launcher = Join-Path $ProjectRoot "launch_mongo_production.ps1"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$StatusPath = Join-Path $RuntimeDir "deployment-status.json"
$LogPath = Join-Path $RuntimeDir "deployment.log"
$Git = $env:MPS_GIT_EXE
if (-not $Git) {
    $envLine = Get-Content (Join-Path $ProjectRoot ".env") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^MPS_GIT_EXE=' } | Select-Object -First 1
    if ($envLine) { $Git = $envLine.Substring(12).Trim().Trim('"').Trim("'") }
}
if (-not $Git -or -not (Test-Path -LiteralPath $Git)) { $Git = (Get-Command git -ErrorAction Stop).Source }

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Write-DeploymentStatus([string]$State, [string]$Message) {
    $record = [ordered]@{status=$State; message=$Message; timestamp=(Get-Date -Format o); branch=$Branch}
    $record | ConvertTo-Json | Set-Content -LiteralPath $StatusPath
    "$(Get-Date -Format o) [$State] $Message" | Add-Content -LiteralPath $LogPath
}

try {
    Write-DeploymentStatus "checking" "Checking production safety and GitHub state."
    $board = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/board" -TimeoutSec 5
    if ($board.settings.run_mode_enabled) { throw "Stop Run Mode before deploying an update." }
    if ($board.robot_motion.active) { throw "Wait for robot motion to finish before deploying an update." }
    $dirty = (& $Git -C $ProjectRoot status --porcelain)
    if ($dirty) { throw "Deployment refused because the working tree has local changes." }

    if (-not $SkipBackup) {
        & $Python -m app.backup_cli create --reason pre-update
        if ($LASTEXITCODE -ne 0) { throw "Deployment refused because the pre-update backup failed." }
    }
    & $Git -C $ProjectRoot fetch --prune $RemoteName $Branch
    if ($LASTEXITCODE -ne 0) { throw "GitHub fetch failed." }
    $local = (& $Git -C $ProjectRoot rev-parse HEAD).Trim()
    $remote = (& $Git -C $ProjectRoot rev-parse "$RemoteName/$Branch").Trim()
    if ($local -eq $remote) {
        Write-DeploymentStatus "current" "The installed version is already current."
        exit 0
    }
    & $Git -C $ProjectRoot merge-base --is-ancestor HEAD "$RemoteName/$Branch"
    if ($LASTEXITCODE -ne 0) { throw "Deployment refused because the GitHub branch is not a fast-forward update." }
    & $Git -C $ProjectRoot pull --ff-only $RemoteName $Branch
    if ($LASTEXITCODE -ne 0) { throw "GitHub fast-forward update failed." }
    & $Python -m pip install -e $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
    Write-DeploymentStatus "restarting" "Update installed; restarting the backend."
    & $Launcher -SkipBrowser
    if ($LASTEXITCODE -ne 0) { throw "Backend restart failed after the update." }
    Write-DeploymentStatus "complete" "GitHub update deployed successfully."
} catch {
    Write-DeploymentStatus "failed" $_.Exception.Message
    exit 1
}
