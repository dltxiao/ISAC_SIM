[CmdletBinding()]
param(
    [int]$ListenPort = 18080,
    [string]$ListenAddress = "0.0.0.0"
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-ListenPort", $ListenPort, "-ListenAddress", $ListenAddress)
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit
}

netsh interface portproxy delete v4tov4 listenport=$ListenPort listenaddress=$ListenAddress 2>$null | Out-Null
Get-NetFirewallRule -DisplayName "ISAC-SIM LAN $ListenPort" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
Write-Host "已移除端口映射与防火墙规则（端口 $ListenPort）。" -ForegroundColor Green
netsh interface portproxy show v4tov4
