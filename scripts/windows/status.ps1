# 只读检查：WSL IP、端口代理、监听、局域网地址与连通性。无需管理员。
[CmdletBinding()]
param(
    [int]$ListenPort = 18080,
    [string]$Distro = "Ubuntu"
)
$wslIp = (wsl.exe -d $Distro hostname -I).Trim().Split()[0]
Write-Host "WSL IP        : $wslIp"
Write-Host ""
Write-Host "端口代理（netsh）："
netsh interface portproxy show v4tov4
Write-Host ""
Write-Host "Windows 监听 $ListenPort："
Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
Write-Host ""
$lan = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.InterfaceAlias -notlike "*WSL*" -and
        $_.InterfaceAlias -notlike "*VMnet*"
    }
$lan | ForEach-Object { Write-Host ("局域网地址   : http://{0}:{1}" -f $_.IPAddress, $ListenPort) }
Write-Host ""
if ($lan) {
    $ip = $lan[0].IPAddress
    $ok = Test-NetConnection -ComputerName $ip -Port $ListenPort -InformationLevel Quiet
    Write-Host ("本机自测 {0}:{1} -> {2}" -f $ip, $ListenPort, $(if ($ok) { "通" } else { "不通" }))
}
