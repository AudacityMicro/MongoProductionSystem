param(
    [ValidateRange(1, 65535)]
    [int]$Port = 50010
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
    Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
    exit
}

$displayName = "Mongo Production System Supervisor"
$existing = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
}
New-NetFirewallRule `
    -DisplayName $displayName `
    -Description "Allows Mongo to initiate the persistent supervisor connection to this application." `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private | Out-Null

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Windows Firewall now allows private-network TCP connections on port $Port.",
    "Mongo supervisor firewall",
    "OK",
    "Information"
) | Out-Null
