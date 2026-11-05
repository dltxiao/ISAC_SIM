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

## 8. 外部检测视角：数据支撑、`/trace` 位置与 `out/` 落点

> 本节假设判定在仿真之外完成：只看导出的观测流与日志，不读 `truth/`、不读 `ctx.extras`。可观测面清单与路径/落盘规则见 [A1 §9.1、§9.4](A1-感知篡改诱导通信拥塞.md)。

### 8.1 判定 A5 需要的数据支撑

A5 是“信令层 + 平台层 + 控制层”的组合：伪造注册打满 AMF、拖慢平台 ingest、再用合法租户 token 下发一条云控制动。三块要分别取证，再按时间对齐。

| 观测面 | 需要捕获的字段 | 在判定中的作用 | 可依赖度 |
|---|---|---|---|
| `core.nas` | `msg_type`、`ue_id`、`gnb_id`、`result`、`cause`、`bot`、`latency_ms` | 注册请求速率（本例约 500/s）、`result=reject`、`cause=congestion`；`amf_overload enter/clear` | `bot` 是仿真标记；速率/拒绝率是协议证据 |
| `core.session` | `ue_id`、`msg_type`、`cause`、`result` | 受害车 `pdu_session_establish_reject` | 协议证据 |
| `app.ingest` | `latency_ms`、`msg_type`、`source`、`result` | 平台 ingest 202 ms（全局），与信令风暴同窗口 | 物理证据 |
| `app.control` | `token_id`、`vehicle_ids`、`command`、`params`、`result` | 单车辆云控制动，来自合法 tenant token | 协议证据 |
| `app.auth` / `app.audit` | `msg_type`、`token_id`、`actor`、`action` | token 校验通过、审计记录 `control:submit` | 协议证据 |
| `control.command` | `src`、`reason`、`params.ttc_s` | `src=platform:V2X-01`、`reason=cloud_control`、`ttc=99` | 物理/协议证据 |
| `link.metrics` | `cbr`、`jam` | 不直接受影响 → 排除 A1/A2 | 反证 |

判定口径：

```text
N1 单位时间注册请求显著高于基线（本例 1 s ≥10 条、实际约 500/s），且 ue_id 高度重复/可预测。
N2 同窗口 core.nas 拒绝率高（cause=congestion），core.session 出现建立拒绝。
N3 app.ingest.latency_ms 持续 >=50（本例 202），与 N1 同窗口。
N4 control.command 的 ttc_s>=10、src=platform:*，但前序没有对应的 CPM/DENM 危险事件。
=> N1∧N3 判 A5；N2 是佐证；N4 是后果与放大器，不是必要条件。
```

边界：

- `bot=true`、`ue_id=bot-*` 是仿真标记；真实系统看速率、身份熵、拒绝码分布；
- 标称速率 1640/s 是 `rate` 参数，实际每 0.1 s 最多 50 条（≈500/s）；
- A5 的 `core.session` 拒绝**不改变会话表**：`core:sessions` 仍显示正常会话，不能拿“会话数没变”否定；
- 平台延迟是全局的（CPM/DENM/SPaT 全都慢），不是只慢受害车相关消息；
- 云控指令在 `t0` 就发出，被延迟的是平台 ingest，不是这条指令。

### 8.2 对应前端 `/trace` 的位置

| 数据 | `/trace` 位置 | 看什么 |
|---|---|---|
| AMF 过载 | **核心网** → `核心网 · Core` 卡 → 子标签「节点」 | AMF 节点变红/橙，指标 `registered/queue_depth/rejected/received`，悬停看 reasons（`overloaded`/`anomaly`/`load 85%`） |
| 信令事件 | 右栏 **TRACE** 的 `core.nas` / `core.session` 事件 | 100 ms 聚合，同 key 显示 `×N`；`registration_request reject (congestion)` |
| 会话被拒 | 同上（`core.session`） | `pdu_session_establish_reject @ ue:<victim>`；注意「会话」表不会变化 |
| 平台延迟 | **平台** → `平台 · V2X` 卡（概览）+ 右栏 `app.ingest` | `ingest_slow` 上升；事件 `latency_ms≈202` |
| 云控 | 平台概览 `control_sent`；右栏 `app.control` | `vehicle_ids=[victim]`、`command=brake` |
| 制动 | **车辆** → `<victim> · state`；右栏 `control.command` | `ttc=99`、`reason=cloud_control`、`speed_drop` |
| 告警 | **总览** → `Detector · alerts`；**底栏时间轴** | `nas_registration_flood`、`ingest_latency_spike`、`brake_without_hazard` |
| 阶段 | 顶栏 **下一步** 后的检查点横幅 | S1 AMF 过载/拒绝（core 层后）→ S2 PDU 会话失败 → S3 平台 Ingest/云控 → S4 远控/物理后果 → S5 告警 |

不在 `/trace` 上的：逐条 NAS 记录（按 100 ms 聚合，右栏只保留最近 120 条、显示 50 条）、`uu.attach`/`uu.handover`/`uu.session`（没有投影）、每条请求的排队时延、AMF 的 `load_hint` 明细（节点卡只显示 4 个指标，reasons 里才有）。

### 8.3 对应后端 `out/` 的位置

| 数据 | 文件 | 过滤键 / 字段 | 备注 |
|---|---|---|---|
| 注册洪泛 | `core.nas.jsonl` | `.payload.msg_type=="registration_request"`、`.payload.bot==true`、`.payload.result=="reject"` | 按 1 s 窗口计数 |
| AMF 过载 | `core.nas.jsonl` | `.payload.msg_type=="amf_overload"`（`enter`/`clear`） | 攻击起止两个点 |
| 会话拒绝 | `core.session.jsonl` | `.payload.msg_type=="pdu_session_establish_reject"` | `ue_id` 是受害车 |
| 平台延迟 | `app.ingest.jsonl` | `.payload.latency_ms>=50` | 本例 202 |
| 云控 | `app.control.jsonl` | `.payload.command=="brake"`、`vehicle_ids` 含受害车 | token 是合法租户 token |
| 鉴权/审计 | `app.auth.jsonl` / `app.audit.jsonl` | `token_validate accept` / `action=="control:submit"` | 与 A9 的区别在 vehicle_ids 规模 |
| 制动 | `control.command.jsonl` | `.payload.reason=="cloud_control"` | `ttc_s=99` |
| 真值（评估侧） | `truth/events.jsonl` | `.campaign=="A5"` | `params.bot_ues/rate` |
| 告警 | `alerts.jsonl` | `nas_registration_flood` / `ingest_latency_spike` / `brake_without_hazard` | 仅 CLI/batch |

```bash
cd out/doc_a5/arterial-14-*
jq -c 'select(.payload.bot==true) | .payload.result' core.nas.jsonl | sort | uniq -c
jq -c 'select(.payload.msg_type=="amf_overload") | {t:.ts_ns, r:.payload.result}' core.nas.jsonl
jq -c 'select(.payload.msg_type=="pdu_session_establish_reject") | .payload.ue_id' core.session.jsonl | head
jq -c 'select(.payload.latency_ms>=50) | {lat:.payload.latency_ms, src:.payload.source}' app.ingest.jsonl | head -3
```

> A5 的“快照类”证据（AMF 健康、队列深度）只在 `/trace` 与内存里；`out/` 只有事件流，需要按 `ts_ns` 重放才能还原当时的健康度。

## 9. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | AMF 过载；平台延迟 200 ms；提交云控 intent |
| 0 | `control.tick()` 发出 `control.command`（ttc=99） |
| 0～ | 每 0.1 s 一批 bot `core.nas` + 1 条 `core.session` 拒绝 |
| +0.1 | 第一条 `app.ingest` 延迟 >=200 ms |
| +1.0 | 检测器满足 10 条/s 门槛，报 `nas_registration_flood` |
| `t1` | `stop()` 清除 AMF 过载与平台延迟 |

## 10. 边界与差异

1. **不是真实注册洪泛**：A5 直接 emit 记录，不创建 UE、不排队、不触发 AUSF 鉴权。
2. **标称速率不等于实际速率**：每 0.1 s 最多 50 条，实际上限约 500/s。
3. **`core.session` 拒绝不改变会话表**：`core:sessions` 仍显示正常会话。
4. **平台延迟是全局的**：所有 ingest 都慢 200 ms，不是只慢受害车相关消息。
5. **云控不“老化”**：`control.command` 在 `t0` 立即发出；`ttc=99` 是攻击者填的值。
6. **`domains` 含 `task`，但没有专门的 task 事件**；这是声明，不是保证。
7. **Trace S0**：A5 的 S0 在 `after_scheduler`；如果在连续运行时已经启动攻击再冻结，会错过 S0；从 `/trace` 注入会自动先冻结。

## 11. 复现与检查

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

## 12. 代码索引

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

## 13. 小结

```text
AMF 过载 + 每 0.1 s 50 条 bot 注册 + 1 条会话拒绝
        │
        ├─ nas_registration_flood
        └─ ingest_latency_spike

platform.submit_control → control.command(ttc=99)
        └─ brake_without_hazard
```

A5 是“核心网/平台语义状态 + 信令记录”的组合攻击：它让 AMF 看起来过载、让平台 ingest 变慢、再叠加一条无依据云控制动。它不是 164 个真实 UE 的协议级洪泛。
