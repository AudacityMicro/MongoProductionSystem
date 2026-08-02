param(
    [Parameter(Mandatory = $true)]
    [string]$BackupRef
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Launcher = Join-Path $ProjectRoot "launch_mongo_production.ps1"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$StagedPath = Join-Path $RuntimeDir ("restore-staged-{0}.db" -f [guid]::NewGuid().ToString("N"))
$RestoreDir = Join-Path $RuntimeDir "restore-backups"
$LogPath = Join-Path $RuntimeDir "restore.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir,$RestoreDir | Out-Null
"$(Get-Date -Format o) Restore requested for $BackupRef" | Add-Content -LiteralPath $LogPath

try {
    & $Python -m app.backup_cli extract --backup-ref $BackupRef --output $StagedPath
    if ($LASTEXITCODE -ne 0) { throw "The portable backup could not be read or validated." }

    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 5
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/system/prepare-shutdown" -ContentType "application/json" -Body "{}" -TimeoutSec 10 | Out-Null
    Start-Sleep -Seconds 1
    Stop-Process -Id ([int]$health.process_id) -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    $databaseUrl = (& $Python -c "from app.settings import settings; print(settings.database_url)").Trim()
    if (-not $databaseUrl.StartsWith("sqlite:///")) { throw "Only the SQLite database can be restored by this tool." }
    $databasePath = $databaseUrl.Substring(10)
    if (-not [IO.Path]::IsPathRooted($databasePath)) { $databasePath = Join-Path $ProjectRoot $databasePath }
    $databasePath = [IO.Path]::GetFullPath($databasePath)
    $archivePath = Join-Path $RestoreDir ("mongo-production-before-restore-{0}.db" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    if (Test-Path -LiteralPath $databasePath) { Move-Item -LiteralPath $databasePath -Destination $archivePath -Force }
    foreach ($suffix in @("-wal", "-shm")) {
        $sidecar = "$databasePath$suffix"
        if (Test-Path -LiteralPath $sidecar) { Move-Item -LiteralPath $sidecar -Destination "$archivePath$suffix" -Force }
    }
    Move-Item -LiteralPath $StagedPath -Destination $databasePath -Force
    & $Launcher -SkipBrowser
    if ($LASTEXITCODE -ne 0) { throw "MPS did not restart after the restore." }
    "$(Get-Date -Format o) Restore completed from $BackupRef" | Add-Content -LiteralPath $LogPath
} catch {
    "$(Get-Date -Format o) Restore failed: $($_.Exception.Message)" | Add-Content -LiteralPath $LogPath
    if (Test-Path -LiteralPath $StagedPath) { Remove-Item -LiteralPath $StagedPath -Force -ErrorAction SilentlyContinue }
    exit 1
}
