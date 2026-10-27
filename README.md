# ISAC-SIM：车联网跨层攻击模拟环境

当前版本：**v0.3.0-dev**（见 [CHANGELOG](CHANGELOG.md)）

单机运行的车联网（V2X）跨层攻击模拟环境：**SUMO 车流 + 自研感知/通信/认证/控制层 + 语义级 5G-A 核心网 + 跨层攻击剧本 + 独立真值 + Web 控制台 + 事件级评估**。

面向任务②"动态安全威胁建模与协同防御研究"的核心指标：

| 指标 | 本环境提供的能力 |
|---|---|
| 攻击识别率 ≥99% | 事件级真值、可检出包络、4 类跨层攻击剧本、盲测切分 |
| 误检率 ≤1% | 难负样本（遮挡/切换/故障/真实急刹/GNSS 退化）、正常场景批量生成 |
| 算法时延 ≤100ms | 发射/到达/判决时间戳、流式接口、P50/P95/P99 报表 |
| 4 类官方攻击 | 感知篡改→拥塞、物理欺骗+控制误导、跨层隐蔽、双因素仿冒 |

## 快速开始

```bash
# 1. 安装（无需 root）
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,dataset]"

# 2. 生成路网（首次或修改 assets 后）
./scripts/build_net.sh

# 3. 查看硬件与容量包络
.venv/bin/python -m isacsim info

# 4. 启动 Web 控制台（浏览器打开 http://127.0.0.1:8080）
.venv/bin/python -m isacsim web --port 8080

# 5. 命令行跑一条攻击
.venv/bin/python -m isacsim run -a A3 --duration 20 --out out

# 6. 批量生成并评估
.venv/bin/python -m isacsim batch --out out/ds --attacks 300 --normal 300 --duration 15 --workers 16 --no-streams
.venv/bin/python -m isacsim eval --runs out/ds
```

## 组成

| 模块 | 说明 |
|---|---|
| `isacsim/world/` | SUMO 后端（TraCI 订阅、动态增删、制动执行器）、几何工具 |
| `isacsim/layers/` | 感知（回波/点迹/目标列表/GNSS）、V2X（CAM/CPM/DENM/SPaT、CBR/DCC）、Uu（gNB 覆盖驱动）、语义核心网、认证指纹、ADAS 控制、任务、图快照 |
| `isacsim/layers/core/` | AMF/SMF/UPF/UDM/AUSF 语义状态机、CoreBus、注册/鉴权/PDU 会话/QoS/TEID |
| `isacsim/attacks/` | A1–A4 跨层剧本 + 8 类单体原语 + 运行时调度器 |
| `isacsim/truth/` | 事件级真值（与检测隔离） |
| `isacsim/detect/` | 参考检测器（分域规则 + 跨层关联，仅用于验证环境） |
| `isacsim/eval/` | 告警聚类、事件匹配、Wilson 置信区间、Markdown 报表 |
| `isacsim/batch/` | 多进程数据集生成 |
| `isacsim/io/` | 原生 JSONL/Parquet、pcapng、5G-A-NIDD 适配器 |
| `isacsim/web/` | FastAPI + SSE + Canvas 控制台 |

## 文档

- [环境与依赖](docs/00-环境与依赖.md)
- [系统架构与数据流](docs/01-系统架构与数据流.md)
- [跨层攻击剧本目录](docs/02-跨层攻击剧本目录.md)
- [指标测量与评估口径](docs/03-指标测量与评估口径.md)
- [算力包络与性能预算](docs/04-算力包络与性能预算.md)
- [Web 控制台使用说明](docs/05-Web控制台使用说明.md)
- [快速上手与演示](docs/06-快速上手与演示.md)
- [实现原理与数据生成](docs/07-实现原理与数据生成.md)
- [更新记录](CHANGELOG.md)
- [WSL 局域网访问](docs/08-WSL局域网访问.md)
- [通信层扩展评估](docs/09-通信层扩展评估.md)
- [通信层语义级拓展详细设计](docs/10-通信层语义级拓展详细设计.md)

## 重要边界

- **参考检测器不是项目交付物**：它是验证"环境可用、指标可达"的基线，最终检测模型由合作单位提供；接口与数据格式对其开放。
- **核心网是语义级**：AMF/SMF/UPF/UDM/AUSF 为进程内状态机，不是 Open5GS/真实协议栈，不承诺 pcap/DPI 兼容；边界见 [详细设计](docs/10-通信层语义级拓展详细设计.md)。
- 感知为**参数化数值模型**（回波参数/点迹/目标列表），不做电磁仿真与图像渲染；"伪造停止牌"以目标级实现。
- 真值只由生成器产出，检测侧不可读；Web 上的真值视图仅供运维演示。
