param(
    [string]$Directory = "runtime/backups"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".env"
$existing = if (Test-Path -LiteralPath $EnvPath) { @(Get-Content -LiteralPath $EnvPath) } else { @() }
$preserved = @($existing | Where-Object {
    $_ -notmatch '^MPS_BACKUP_' -and $_ -notmatch '^MPS_GITHUB_TOKEN=' -and $_ -notmatch '^MPS_GIT_EXE='
})
$settings = @(
    "MPS_BACKUP_ENABLED=true",
    "MPS_BACKUP_DIRECTORY=$Directory",
    "MPS_BACKUP_INTERVAL_SECONDS=900",
    "MPS_BACKUP_DEBOUNCE_SECONDS=15"
)
Set-Content -LiteralPath $EnvPath -Value @($preserved + $settings) -Encoding utf8
$BackupPath = if ([IO.Path]::IsPathRooted($Directory)) { $Directory } else { Join-Path $ProjectRoot $Directory }
New-Item -ItemType Directory -Force -Path $BackupPath | Out-Null
Write-Output "Portable backup configuration written to $EnvPath"
Write-Output "Backup directory: $Directory"
