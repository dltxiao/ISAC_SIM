# 跨层攻击实现解析（A1–A10）

> 代码基线：`feature/trace-watch`（v0.3.0 + A1–A10 TraceProfile），2026-11-03。
> 本目录按“代码走读”方式解释每个攻击剧本：入口、选目标、注入点、下游消费、真值、检测、时序、边界与复现。
> 共享机制（`AttackSpec`、调度器、HookBus、`ctx.emit`、真值落盘）以 [A1](A1-感知篡改诱导通信拥塞.md) §2–§4 为准，A2–A10 只写差异。

## 文档索引

| 编号 | 剧本 | 文档 |
|---|---|---|
| A1 | 感知数据篡改诱导通信拥塞 | [A1](A1-感知篡改诱导通信拥塞.md) |
| A2 | 物理环境欺骗 + 控制指令误导 | [A2](A2-物理环境欺骗与控制误导.md) |
| A3 | 跨层隐蔽攻击（雷达回波渐偏） | [A3](A3-跨层隐蔽攻击.md) |
| A4 | 双因素仿冒攻击 | [A4](A4-双因素仿冒.md) |
| A5 | 5G-A 信令风暴 | [A5](A5-5G-A信令风暴.md) |
| A6 | 切片/QoS 抢占 | [A6](A6-切片QoS抢占.md) |
| A7 | UPF/N6 流量劫持 | [A7](A7-UPF-N6流量劫持.md) |
| A8 | NEF/订阅篡改 | [A8](A8-NEF订阅篡改.md) |
| A9 | 平台越权/云控注入 | [A9](A9-平台越权与云控注入.md) |
| A10 | 跨层隐蔽组合 | [A10](A10-跨层隐蔽组合.md) |

## 一眼看全

| 编号 | 核心注入点 | 直接后果 | TraceProfile |
|---|---|---|---|
| A1 | `sensing.objects` 追加幽灵；`v2x_extra_load`；伪造 DENM | ADAS 可能制动；CBR/PER 上升 | S1–S5 |
| A2 | `sensing.objects` 追加停止牌；`control.inject_brake`；`link_jam` | 直接制动；链路质量下降 | S0–S5 |
| A3 | `sensing.radar.echo` 距离渐偏；60% 处小幅制动 | 点迹偏移；轻微减速 | S0–S5 |
| A4 | 克隆 `auth.cert`/`auth.fingerprint`；伪标识 CAM | 位置跳变/指纹矛盾 | S0–S4 |
| A5 | AMF 过载、bot 注册、PDU 会话失败、平台延迟、云控 | 核心网过载；远控延迟 | S0–S5 |
| A6 | SMF 5QI 79→9；平台 ingest 延迟；伪造 CPM；制动 | QoS 降级；平台延迟 | S0–S5 |
| A7 | UPF 路由改到 `dn:EVIL-01`；伪造 CPM；制动 | 路由漂移；溯源不符 | S0–S4 |
| A8 | NEF/平台退订 DENM/SPaT；平台延迟；制动 | 平台漏事件 | S0–S5 |
| A9 | 复用合法租户 token；批量云控制动 | 多车同时减速 | S0–S5 |
| A10 | 雷达渐偏 + 轻度信令 + 审计抑制 + 小制动 | 隐蔽组合；审计缺口 | S0–S5 |

## 共同代码路径

```text
POST /api/attacks
  → SessionManager.submit_attack()
  → Simulation.submit_attack()
  → AttackScheduler.submit()
  → CampaignA<n>(...)
      ├─ start(ctx): 选目标、注册 hook、写初始事件
      ├─ tick(ctx): 每个 tick 注入/推进
      └─ stop(ctx): 摘 hook、清理全局状态
  → AttackScheduler.tick(): truth.add(inst.truth())
```

每个 tick 的层序（[engine.py](../../isacsim/core/engine.py:123)）：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control → task → edge
```

因此：

- `scheduler.tick()` 里写入的 `ctx.extras` 能被同一 tick 的 sensing/v2x/core/platform 读到；
- `control.tick()` 在 v2x 之后，ADAS 自己产生的 `brake_events` 不会回到同一 tick 的 v2x；
- A2/A3/A6/A7/A8/A10 直接调用 `control.inject_brake()`，所以不依赖 ADAS。

## 读文档前需要知道的三件事

1. **真值 `stages` 是计划，不是运行时开关**。代码里没有“到 S3 才执行”的分支；阶段只用于讲解和 TraceProfile。
2. **`domains` 是声明**。某个 seed 下如果目标不满足条件，声明的域仍可能出现“没有对应事件”的情况。
3. **本目录的实测数字来自 seed=14、`amplitude=0.8`、`start=2.0`、`duration=3.0` 的一次本机运行**；换 SUMO 版本、路网或车流后绝对值会变，但代码路径和字段不变。

## 复现入口

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A2 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a2
```

Web 端在 `/trace` 的“攻击注入”条选择 A2–A10，运行中注入会自动冻结，便于从 S0 单步。

## 文档之外

- 追踪页如何按 profile 暂停：见 [Trace 设计](../trace/README.md)；
- 数据字段级说明：见 [实现原理与数据生成](../07-实现原理与数据生成.md)；
- 检测规则与指标口径：见 [指标测量与评估口径](../03-指标测量与评估口径.md)。
