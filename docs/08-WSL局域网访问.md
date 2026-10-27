# WSL 局域网访问

本机环境：Windows 10（10.0.19045）+ WSL 2（3.0.3.0，Ubuntu），WSL 处于 **NAT 模式**。
Web 服务在 WSL 内监听 `0.0.0.0:8080`，Windows 通过 `localhost:8080` 可以访问（WSL 的 localhost 转发），
但**局域网其他 PC 不能**直接访问 WSL 的 `172.31.x.x`。

本文记录把服务暴露到 Windows 宿主机局域网的做法。当前已验证可用的地址：

```
http://192.168.100.102:18080
```

## 1 拓扑与端口选择

```
局域网 PC ──► Windows 宿主机 192.168.100.102:18080
                    │  netsh portproxy（iphlpsvc）
                    ▼
              WSL 2 172.31.203.187:8080
                    │  isacsim web --host 0.0.0.0
                    ▼
              控制台 / 仿真
```

- **为什么用 18080**：Windows 上 `8080` 已被 `wslrelay` 占用（它把 `127.0.0.1:8080` 转发到 WSL），
  再让 portproxy 监听 `0.0.0.0:8080` 容易冲突；改用 `18080` 最稳。
- **为什么不用 mirrored 网络**：`networkingMode=mirrored` 需要 Windows 11 22H2+；本机是 Windows 10 19045，不支持。
- **为什么必须管理员**：`netsh interface portproxy` 需要提升权限；WSL 内无法直接改 Windows 网络配置。

## 2 操作步骤

### 2.1 在 WSL 内启动服务

```bash
cd ~/AIWork/ISAC_SIM
nohup .venv/bin/python -m isacsim web --host 0.0.0.0 --port 8080 \
  > out/web.log 2>&1 & echo $! > out/web.pid
```

确认监听 `0.0.0.0:8080`：

```bash
ss -ltn | grep 8080
```

### 2.2 在 Windows 上运行端口映射脚本

脚本已放在：

```
C:\Users\zhongxiao\isacsim\enable-lan.ps1
```

运行方式（任选）：

- 文件资源管理器里右键 → **使用 PowerShell 运行**；
- 或在 PowerShell 中执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\zhongxiao\isacsim\enable-lan.ps1
```

脚本会：

1. 自动检测 WSL 当前 IP（`wsl -d Ubuntu hostname -I`）；
2. 删除旧的 18080 映射并重建：`0.0.0.0:18080 → <WSL_IP>:8080`；
3. 添加入站防火墙规则 `ISAC-SIM LAN 18080`（Domain/Private）；
4. 打印可用的局域网地址。

脚本会自行请求管理员权限（UAC 点“是”）。

### 2.3 从局域网 PC 访问

```
http://192.168.100.102:18080
```

Windows 本机仍可用 `http://localhost:8080`。

## 3 验证

在 WSL 内：

```bash
# 端口映射是否生效
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.100.102:18080/
```

在 Windows PowerShell（无需管理员）：

```powershell
C:\Users\zhongxiao\isacsim\status.ps1
```

会显示 WSL IP、portproxy 表、18080 监听、局域网地址与连通性。

在局域网 PC：

```powershell
Test-NetConnection 192.168.100.102 -Port 18080
```

浏览器打开 `http://192.168.100.102:18080`。

## 4 关闭映射

管理员 PowerShell：

```powershell
C:\Users\zhongxiao\isacsim\disable-lan.ps1
```

或手动：

```powershell
netsh interface portproxy delete v4tov4 listenport=18080 listenaddress=0.0.0.0
Remove-NetFirewallRule -DisplayName "ISAC-SIM LAN 18080"
```

## 5 WSL 重启之后

WSL 的 IP 会变化（`172.31.x.x`），映射会失效。恢复顺序：

1. 在 WSL 内重新启动 Web 服务（见 2.1）；
2. 在 Windows 上重新运行 `enable-lan.ps1`。

若希望登录时自动执行，可用管理员 PowerShell 创建计划任务（可选）：

```powershell
schtasks /Create /TN "ISAC-SIM LAN" /SC ONLOGON /RL HIGHEST `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\zhongxiao\isacsim\enable-lan.ps1"
```

> 任务在登录时执行，若此时 WSL 尚未启动，`wsl.exe` 会拉起它，启动会慢几秒。

## 6 故障排查

| 现象 | 检查 | 处理 |
|---|---|---|
| 局域网打不开，Windows 本机 `localhost:8080` 正常 | `netsh interface portproxy show all` 是否有 18080 | 重跑 `enable-lan.ps1` |
| 端口映射在、仍打不开 | Windows 防火墙是否拦截 18080 | 已加规则；若手动开了 Public 配置，需补规则或改用 `-Profile Any` |
| WSL 重启后失效 | WSL IP 是否变化 | 重跑脚本 |
| 端口冲突 | `Get-NetTCPConnection -LocalPort 18080` | 换 `-ListenPort` 参数 |
| 服务不可达 | WSL 内 `ss -ltn \| grep 8080` | 服务没起或只绑了 127.0.0.1 |
| `iphlpsvc` 未运行 | `Get-Service iphlpsvc` | `Start-Service iphlpsvc` |

## 7 安全说明

- 控制台**没有登录鉴权**：能访问该端口的人可以启停仿真、注入攻击、下载 `out/web/` 下的运行产物。
- 建议只在可信局域网开放；可把防火墙规则的远程地址限制到本网段：

  ```powershell
  Set-NetFirewallRule -DisplayName "ISAC-SIM LAN 18080" -RemoteAddress 192.168.100.0/24
  ```

- 若需从公网访问，请不要直接映射端口；优先 SSH 隧道/VPN，或先给 Web 控制台加访问令牌。
- 映射监听在 `0.0.0.0`，会同时出现在 VMware 虚拟网卡等接口上；如需最小暴露，可把 `-ListenAddress` 改为局域网 IP（注意 DHCP 变化）。

## 8 当前实测记录

| 项 | 值 |
|---|---|
| Windows 版本 | 10.0.19045（WSL 3.0.3.0） |
| 局域网 IP / 网关 | 192.168.100.102 / 192.168.100.1（Private） |
| WSL IP | 172.31.203.187（NAT） |
| 端口映射 | `0.0.0.0:18080 → 172.31.203.187:8080` |
| 防火墙规则 | `ISAC-SIM LAN 18080`，TCP 18080，Domain/Private |
| Windows → 局域网地址 | HTTP 200 |
| WSL → 局域网地址 | HTTP 200 |
