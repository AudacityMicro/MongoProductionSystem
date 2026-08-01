param(
    [ValidateRange(1, 65535)]
    [int]$Port = 50011
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
    Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
    exit
}

$displayName = "Mongo Production System Mill Supervisor"
try {
    Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule `
        -DisplayName $displayName `
        -Description "Allows PathPilot to initiate the staged persistent mill supervisor connection." `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Private | Out-Null
    Write-Host "Firewall rule installed: $displayName on private TCP port $Port." -ForegroundColor Green
    Write-Host "The mill supervisor remains telemetry-only until it is explicitly activated." -ForegroundColor Yellow
} catch {
    Write-Host "Firewall rule installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Read-Host "Press Enter to close this window"
}
