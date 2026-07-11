param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LaunchScript = Join-Path $ProjectRoot "launch_mongo_production.ps1"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$RestartLog = Join-Path $RuntimeDir "restart.log"
$LauncherStdout = Join-Path $RuntimeDir "restart-launch.stdout.log"
$LauncherStderr = Join-Path $RuntimeDir "restart-launch.stderr.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
"$(Get-Date -Format o) Restart helper started." | Set-Content -LiteralPath $RestartLog

try {
    Start-Sleep -Seconds 1
    $launcher = Start-Process powershell.exe `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $LaunchScript),
            "-SkipBrowser"
        ) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LauncherStdout `
        -RedirectStandardError $LauncherStderr `
        -Wait `
        -PassThru
    if ($launcher.ExitCode -ne 0) {
        throw "Launcher exited with code $($launcher.ExitCode)."
    }
    "$(Get-Date -Format o) Launcher completed: $($launcher.Id)." | Add-Content -LiteralPath $RestartLog
} catch {
    "$(Get-Date -Format o) Restart helper failed: $($_.Exception.Message)" | Add-Content -LiteralPath $RestartLog
    exit 1
}
