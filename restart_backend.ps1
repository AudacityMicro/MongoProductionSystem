param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LaunchScript = Join-Path $ProjectRoot "launch_mongo_production.ps1"

Start-Sleep -Seconds 1
& $LaunchScript -SkipBrowser
