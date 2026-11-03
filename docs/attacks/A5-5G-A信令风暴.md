# A5 5G-A 信令风暴：代码走读与实现解析

> 代码基线：`feature/trace-watch`（v0.3.0 + A1–A10 TraceProfile），2026-11-03。
> 共享机制见 [A1 §2–§4](A1-感知篡改诱导通信拥塞.md)；本文只写 A5 的差异。
> 目标：解释“伪 UE 注册洪泛 → AMF 过载 → PDU 会话失败 → 平台延迟 → 云控”在代码里真正发生了什么，以及标称速率与实际发射速率的差异。

## 1. 一句话概括

A5 做四件事：

1. `core.amf.set_overload(ctx, True)` 把 AMF 标成过载；
2. 每个 0.1 s 注入一批 `core.nas` bot 注册，并额外发一条 `core.session` 拒绝；
3. 把 `ctx.extras["platform_ingest_delay_ms"]` 设为 200，拖慢平台 ingest；
4. 用平台租户 token 下发一次云控制动（`decel=2.0, duration=1.0`）。

它**不**真的创建 164 个 UE，也不真的让 AMF 队列排队；bot 注册是直接 emit 的记录，`core.session` 拒绝也是攻击者直接写的，不是 SMF 状态机拒绝。

## 2. 端到端调用链

```text
POST /api/attacks {type:"A5", amplitude, duration, start}
  → CampaignA5.start(ctx)                 campaigns.py:364
      ├─ 需要 core + platform
      ├─ pick_vehicle(seed,"a5")
      ├─ core.amf.set_overload(True)      amf.py:24
      ├─ extras["platform_ingest_delay_ms"]=200
      └─ platform.submit_control(token, brake 2.0/1.0)
  → 每 0.1 s CampaignA5.tick(ctx)         campaigns.py:387
      ├─ 保持 platform_ingest_delay_ms=200
      ├─ n = min(50, int(rate*0.1))
      ├─ n 条 core.nas registration_request(result=reject, bot=true)
      └─ 1 条 core.session pdu_session_establish_reject
  → stop(ctx)                             campaigns.py:414
      ├─ amf.set_overload(False)
      └─ 清除 platform_ingest_delay_ms
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

- `core.nas`/`core.session` 由 A5 在 scheduler 直接 emit，不经过 AMF/SMF 状态机；
- `platform_ingest_delay_ms` 在 scheduler 写入，同一 tick 的 platform ingest 读到；
- `submit_control` 在 start 里调用，intent 由 `control.tick()` 在 platform 之后消费。

## 3. 参数

```python
self.bot_ues = 20 + int(round(180 * max(0.0, min(1.0, self.spec.amplitude))))
self.rate = 200.0 + 1800.0 * max(0.0, min(1.0, self.spec.amplitude))
```

| `a` | `bot_ues` | 标称 `rate` | 每 0.1 s 发射 `n` | 理论最大实际速率 |
|---:|---:|---:|---:|---:|
| 0.5 | 110 | 1100/s | 50 | 500/s |
| 0.8 | 164 | 1640/s | 50 | 500/s |
| 1.0 | 200 | 2000/s | 50 | 500/s |

`n = max(1, min(50, int(self.rate * 0.1)))`。所以：

- 真值里的 `rate` 是**标称强度**，不是实际 emit 速率；
- 实际 `core.nas` 最多约 500 条/s；
- 每次 `tick` 还会发 1 条 `core.session` 拒绝，约 10 条/s。

seed=14、a=0.8、3 s 攻击实测 `core.nas` 约 1029 条，远低于 `1640×3` 的理论值。

## 4. 注入点

### 4.1 AMF 过载

```python
core.amf.set_overload(ctx, True)
```

`AMF.set_overload()`（[amf.py](../../isacsim/layers/core/amf.py:24)）设置：

- `overloaded=True`；
- `load_hint=0.85`；
- `queue_depth=queue_capacity×0.85`；
- emit 一条 `core.nas`：`msg_type=amf_overload, result=enter`。

这会让 CoreProvider 的 `core:amf` 健康状态变红/橙，也会让 trace 的 A5 S1 命中。

### 4.2 bot 注册

```python
ctx.emit("core.nas", f"ue:bot-{i % self.bot_ues:04d}", "amf:AMF-01", {
    "msg_type": "registration_request",
    "ue_id": f"bot-{i % self.bot_ues:04d}",
    "gnb_id": "GNB-01",
    "result": "reject",
    "cause": "congestion",
    "bot": True,
    "latency_ms": 12.0,
})
```

- 直接 emit，不调用 `amf.start_registration()`；
- `bot=True` 是检测器识别洪泛的关键字段；
- `result=reject`、`cause=congestion` 是攻击者填的，不是 AMF 实际拒绝；
- `ue_id` 在 `bot_ues` 范围内循环，不是每个都唯一。

### 4.3 PDU 会话失败

```python
ctx.emit("core.session", f"ue:{self.veh_id}", "smf:SMF-01", {
    "msg_type": "pdu_session_establish_reject",
    "ue_id": self.veh_id,
    "result": "reject",
    "cause": "congestion",
    "bot": True,
    "latency_ms": 18.0,
})
```

同样直接 emit；它不会真的删除 SMF 里的 session，也不会改变 `core:sessions` 表。

### 4.4 平台延迟与云控

```python
ctx.extras["platform_ingest_delay_ms"] = 200.0
...
platform.submit_control(ctx, token_id=token, vehicle_ids=[self.veh_id],
    command="brake", params={"decel_mps2": 2.0, "duration_s": 1.0}, ttl_s=10.0)
```

- 平台 ingest 的真实延迟变成 `2 + 200 = 202 ms`；
- 云控走 `Dispatcher.submit()`（[dispatch.py](../../isacsim/layers/platform/dispatch.py:17)），会 emit `app.control` 和 `app.audit`；
- 控制 intent 由 `ControlLayer` 消费，发出 `control.command`，`ttc=99`、`src=platform:V2X-01`。

## 5. 下游消费

| 消费者 | 看到什么 | 结果 |
|---|---|---|
| CoreProvider | AMF 过载、queue_depth、health | `core:amf` 变红/橙 |
| Platform | ingest 延迟 202 ms | `app.ingest` 出现 `latency_ms>=50` |
| Control | 云控 intent | 一条 `control.command`（ttc=99） |
| Detector | bot 注册、ingest 延迟、无依据制动 | `nas_registration_flood`、`ingest_latency_spike`、`brake_without_hazard` |
| Trace | 100 ms 聚合的 `core.nas`、`core.session`、`app.*` | A5 S0–S5 |

## 6. 真值

`CampaignA5.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:421)）实测：

```json
{
  "nodes": ["amf:AMF-01", "smf:SMF-01", "platform:V2X-01",
            "veh:westbound.0", "attacker:A5"],
  "domains": ["uu", "core", "app", "task", "control"],
  "params": {"bot_ues": 164, "rate": 1640.0, "victim": "westbound.0"},
  "envelope": {"in_envelope": true, "criterion": "rate >= 1000/s"},
  "stages": [
    {"name": "注册洪泛", "t0": 2.0, "t1": 3.0},
    {"name": "PDU 会话失败", "t0": 3.0, "t1": 5.0},
    {"name": "远控指令老化", "t0": 5.0, "t1": 5.0}
  ],
  "causal_chain": [
    "core.nas:registration_flood",
    "core.session:establish_fail",
    "app.ingest:age_up",
    "control.command:delay"
  ]
}
```

三个“不要误读”：

1. `rate=1640` 是标称值，实际最多约 500 条/s；
2. `stages` 的“远控指令老化”是一个零长度区间（duration=3 时 `t0+3=t1`），代码里没有“老化”逻辑；
3. 真值说 `control.command:delay`，但实测 `control.command` 在 `t0` 就发出（`ttc=99`）；被延迟的是平台 ingest，不是这条控制指令。

## 7. 检测侧看到了什么

| 规则 | 触发 |
|---|---|
| `nas_registration_flood` | 1 s 内 ≥10 条 `bot=true` 的 `registration_request` |
| `ingest_latency_spike` | `app.ingest.latency_ms >= 50` |
| `brake_without_hazard` | `control.command.params.ttc_s >= 10` |
| 其他 | `core.session` 拒绝本身没有专用规则 |

seed=14、a=0.8、3 s 攻击实测：

```text
nas_registration_flood ×3, ingest_latency_spike ×3,
brake_without_hazard ×1
```

## 8. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | AMF 过载；平台延迟 200 ms；提交云控 intent |
| 0 | `control.tick()` 发出 `control.command`（ttc=99） |
| 0～ | 每 0.1 s 一批 bot `core.nas` + 1 条 `core.session` 拒绝 |
| +0.1 | 第一条 `app.ingest` 延迟 >=200 ms |
| +1.0 | 检测器满足 10 条/s 门槛，报 `nas_registration_flood` |
| `t1` | `stop()` 清除 AMF 过载与平台延迟 |

## 9. 边界与差异

1. **不是真实注册洪泛**：A5 直接 emit 记录，不创建 UE、不排队、不触发 AUSF 鉴权。
2. **标称速率不等于实际速率**：每 0.1 s 最多 50 条，实际上限约 500/s。
3. **`core.session` 拒绝不改变会话表**：`core:sessions` 仍显示正常会话。
4. **平台延迟是全局的**：所有 ingest 都慢 200 ms，不是只慢受害车相关消息。
5. **云控不“老化”**：`control.command` 在 `t0` 立即发出；`ttc=99` 是攻击者填的值。
6. **`domains` 含 `task`，但没有专门的 task 事件**；这是声明，不是保证。
7. **Trace S0**：A5 的 S0 在 `after_scheduler`；如果在连续运行时已经启动攻击再冻结，会错过 S0；从 `/trace` 注入会自动先冻结。

## 10. 复现与检查

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A5 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a5
```

| 文件 | 看什么 |
|---|---|
| `core.nas.jsonl` | `bot=true`、`result=reject`、`cause=congestion` |
| `core.session.jsonl` | `pdu_session_establish_reject` |
| `app.ingest.jsonl` | `latency_ms≈202` |
| `app.control.jsonl` | `vehicle_ids=[victim]`、`params.decel_mps2=2.0` |
| `control.command.jsonl` | `src=platform:V2X-01`、`ttc=99` |
| `truth/events.jsonl` | `params.bot_ues/rate` |
| `alerts.jsonl` | `nas_registration_flood`、`ingest_latency_spike` |

## 11. 代码索引

| 作用 | 位置 |
|---|---|
| A5 类与 `start/tick/stop/truth` | `isacsim/attacks/campaigns.py:350`, `:364`, `:387`, `:414`, `:421` |
| AMF 过载 | `isacsim/layers/core/amf.py:24` |
| 平台云控 | `isacsim/layers/platform/dispatch.py:17` |
| 平台 ingest 延迟 | `isacsim/layers/platform/ingest.py:42` |
| `nas_registration_flood` | `isacsim/detect/reference.py:196` |
| `ingest_latency_spike` | `isacsim/detect/reference.py:218` |
| `brake_without_hazard` | `isacsim/detect/reference.py:190` |
| A5 真值链 | `isacsim/truth/model.py:34` |
| A5 profile | `isacsim/trace/profiles.py:261` |

## 12. 小结

```text
AMF 过载 + 每 0.1 s 50 条 bot 注册 + 1 条会话拒绝
        │
        ├─ nas_registration_flood
        └─ ingest_latency_spike

platform.submit_control → control.command(ttc=99)
        └─ brake_without_hazard
```

A5 是“核心网/平台语义状态 + 信令记录”的组合攻击：它让 AMF 看起来过载、让平台 ingest 变慢、再叠加一条无依据云控制动。它不是 164 个真实 UE 的协议级洪泛。
