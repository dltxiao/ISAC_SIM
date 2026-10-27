# 让 Windows 宿主机局域网访问 WSL 内的 ISAC-SIM Web 控制台。
# 用法：右键“使用 PowerShell 运行”，或在 PowerShell 中执行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\enable-lan.ps1
# 需要管理员权限（脚本会自动请求 UAC）。
[CmdletBinding()]
param(
    [int]$ListenPort = 18080,          # Windows 侧对外端口（8080 被 wslrelay 占用，故用 18080）
    [int]$TargetPort = 8080,           # WSL 内服务端口
    [string]$Distro = "Ubuntu",
    [string]$ListenAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "需要管理员权限，正在请求提升；请在 UAC 对话框点“是”..." -ForegroundColor Yellow
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-ListenPort", $ListenPort, "-TargetPort", $TargetPort,
        "-Distro", $Distro, "-ListenAddress", $ListenAddress
    )
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit
}

# WSL 当前 IP（重启 WSL 后会变化，需重跑本脚本）
$wslIp = (wsl.exe -d $Distro hostname -I).Trim().Split()[0]
if (-not $wslIp) { throw "无法获取 WSL IP，请先确认发行版名称：wsl -l -v" }
Write-Host "WSL IP: $wslIp"

# 端口代理：Windows 0.0.0.0:ListenPort -> WSL:TargetPort
netsh interface portproxy delete v4tov4 listenport=$ListenPort listenaddress=$ListenAddress 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenport=$ListenPort listenaddress=$ListenAddress connectport=$TargetPort connectaddress=$wslIp | Out-Null

# 防火墙放行（Private/Domain；Public 不放行）
$ruleName = "ISAC-SIM LAN $ListenPort"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $ListenPort -Profile Domain,Private | Out-Null

Write-Host ""
Write-Host "已配置端口映射：" -ForegroundColor Green
netsh interface portproxy show v4tov4
Write-Host ""
Write-Host "局域网访问地址：" -ForegroundColor Green
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.InterfaceAlias -notlike "*WSL*" -and
        $_.InterfaceAlias -notlike "*VMnet*"
    } |
    ForEach-Object { Write-Host ("  http://{0}:{1}" -f $_.IPAddress, $ListenPort) }
Write-Host ""
Write-Host "提示：WSL 重启后 IP 会变化，请重新运行本脚本；WSL 内的 web 服务也要在重启后启动。" -ForegroundColor Yellow
