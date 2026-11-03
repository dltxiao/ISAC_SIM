# A6 切片/QoS 抢占：代码走读与实现解析

> 代码基线：`feature/trace-watch`（v0.3.0 + A1–A10 TraceProfile），2026-11-03。
> 共享机制见 [A1 §2–§4](A1-感知篡改诱导通信拥塞.md)；本文只写 A6 的差异。
> 目标：解释“5QI 79→9 + 平台 ingest 延迟 + 伪造 CPM + 制动”的真实代码路径，以及哪些只是语义状态、没有进入真实调度。

## 1. 一句话概括

A6 做四件事：

1. 选一个已有 PDU 会话的 UE，调用 `smf.modify_qos(five_qi=9, gfbr=100)`；
2. 把 `platform_ingest_delay_ms` 设为 80，拖慢所有平台 ingest；
3. 发一条 `v2x.cpm`，带 `degraded=true, lost=true, qos=5qi-9`；
4. 直接 `inject_brake(decel=2.0, duration=1.0)`。

QoS 修改是**语义状态**：`core:sessions` 表会显示 5QI=9，但当前没有真实调度器/队列按 QoS 降速；平台延迟是全局变量。

## 2. 端到端调用链

```text
POST /api/attacks {type:"A6", amplitude, duration, start}
  → CampaignA6.start(ctx)                 campaigns.py:598
      ├─ 需要 core + platform，且 core.smf.sessions 非空
      ├─ 从 sessions 里按 seed 选 victim
      ├─ core.smf.modify_qos(victim, five_qi=9, gfbr_kbps=100)
      ├─ extras["platform_ingest_delay_ms"]=80
      └─ 写一次 CPM + inject_brake(2.0, 1.0) 在 tick 里
  → CampaignA6.tick(ctx)                  campaigns.py:615
      ├─ 保持 platform_ingest_delay_ms=80
      └─ 第一次 tick：emit v2x.cpm + control.inject_brake
  → stop(ctx)                             campaigns.py:636
      ├─ smf.modify_qos(victim, five_qi=79, gfbr=1000)
      └─ 清除平台延迟
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

`modify_qos` 在 scheduler 调用，但事件 `core.qos` 直接 emit；A6 不依赖 core.tick 的状态机。

## 3. 选目标与参数

```python
if core is None or platform is None or not core.smf.sessions:
    self.finished = True
    return
sessions = sorted(core.smf.sessions)
self.veh_id = sessions[self.seed % len(sessions)]
if self.veh_id not in ctx.vehicles:
    self.finished = True
```

- 只选**已有 PDU 会话**的 UE，保证 `modify_qos` 有落点；
- 如果当前还没有会话，A6 直接结束；
- `self.bias` 字段存在但没有使用；
- `amplitude` 不影响 QoS/延迟/制动幅度；A6 的强度维度基本固定。

| 量 | 值 |
|---|---:|
| 5QI | 79 → 9 |
| GFBR | 1000 → 100 kbps |
| 平台 ingest 延迟 | 80 ms（+2 ms 基础） |
| CPM 延迟字段 | 120 ms |
| 制动 | 2.0 m/s²，1.0 s |

## 4. 注入点

### 4.1 QoS 修改

`SMF.modify_qos()`（[smf.py](../../isacsim/layers/core/smf.py:100)）：

```python
qos.five_qi = int(five_qi)
qos.gfbr_kbps = int(gfbr_kbps)
if int(five_qi) <= 9:
    self.degraded.add(ue_id)
ctx.emit("core.qos", self.entity, session.upf_id, {
    "msg_type": "qos_flow_modify",
    "ue_id": ue_id,
    "5qi": int(five_qi),
    "old_5qi": old,
    "gfbr_kbps": int(gfbr_kbps),
    "result": "accept",
})
```

它只改 `PDUSession.qos_flows[1]` 和 `degraded` 集合；不会改 UPF 的队列、不会丢包、不会实际降速。

### 4.2 平台延迟

```python
ctx.extras["platform_ingest_delay_ms"] = 80.0
```

`PlatformLayer.tick()` 的 ingest 会读这个值，`latency = 2 + 80 = 82 ms`；所有 CPM/DENM/SPaT 都受影响，不只 victim。

### 4.3 伪造/降级 CPM

```python
ctx.emit("v2x.cpm", "ATK-A6", "attacker:A6", {
    "msg_type": "cpm",
    "sender": "ATK-A6",
    "object_count": 1,
    "objects": [{"object_id": 6001, "cls": "obstacle",
                 "x": veh.x + 25.0, "y": veh.y}],
    "delay_ms": 120.0, "lost": True, "degraded": True,
    "qos": "5qi-9", "forged": True,
})
```

- 直接 emit，不进入 `v2x_outbox`，所以平台不会自动 ingest 它；
- `lost=True` 是攻击者填的标记，没有真的丢失；
- `qos=5qi-9` 是字符串标记，不是从 SMF 查出来的。

### 4.4 制动

```python
control.inject_brake(ctx, self.veh_id, decel=2.0, duration=1.0)
```

`reason=injected`、`ttc=99`、`src=attacker:ctl-*`。

## 5. 下游消费

| 消费者 | 看到什么 |
|---|---|
| CoreProvider | `core:smf` 的 `degraded_sessions`、`core:sessions` 的 `five_qi=9` |
| Platform | 所有 ingest 82 ms；`app.ingest` 延迟告警 |
| Control | 一条 `control.command` |
| Detector | `qos_downgrade`、`ingest_latency_spike`、`brake_without_hazard` |
| Trace | `core.qos`、`v2x.cpm.forged`（A6 事件桥）、`app.ingest`、`control.command` |

## 6. 真值

`CampaignA6.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:643)）实测：

```json
{
  "nodes": ["smf:SMF-01", "upf:UPF-01", "platform:V2X-01",
            "veh:southbound.0", "attacker:A6"],
  "domains": ["core", "app", "v2x", "control"],
  "params": {"5qi_from": 79, "5qi_to": 9, "victim": "southbound.0"},
  "envelope": {"in_envelope": true, "criterion": "5QI downgrade or latency >= 50ms"},
  "stages": [
    {"name": "QoS 降级", "t0": 2.0, "t1": 3.0},
    {"name": "CPM 延迟/丢失", "t0": 3.0, "t1": 5.0}
  ],
  "causal_chain": [
    "core.qos:downgrade",
    "upf:queue_up",
    "v2x.cpm:loss",
    "control.command:late"
  ]
}
```

`causal_chain` 里的 `upf:queue_up` 没有对应代码；UPF 只改了 route/flow 表，没有队列增长。

## 7. 检测侧看到了什么

| 规则 | 触发 | 实测 |
|---|---|---|
| `qos_downgrade` | `qos_flow_modify` 且 5qi<=9 | 1 条 |
| `ingest_latency_spike` | `app.ingest.latency_ms>=50` | 3 条 |
| `brake_without_hazard` | `ttc_s>=10` | 1 条 |
| CPM 专用规则 | 无 | — |

seed=14、a=0.8、3 s 攻击：

```text
qos_downgrade ×1, ingest_latency_spike ×3, brake_without_hazard ×1
```

## 8. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | `modify_qos(9, 100)` 并 emit `core.qos`；平台延迟 80 |
| +0.05 | 第一帧 CPM 降级 + 制动 |
| +0.1 | `app.ingest` 延迟 82 ms |
| +0.17 | SUMO slowDown 落地 |
| `t1` | 恢复 5QI=79/1000；清除延迟 |

## 9. 边界与差异

1. **QoS 只有语义状态**：没有真实队列/调度器按 5QI 降速。
2. **平台延迟是全局的**：所有 ingest 都慢，不是只针对 CPM。
3. **伪造 CPM 不进平台**：直接 emit，平台不会自动消费；`lost=true` 没有真实丢失。
4. **`upf:queue_up` 不存在**：真值链里的这一环没有实现。
5. **`amplitude` 基本无效**：A6 的 QoS/延迟/制动都是固定值。
6. **`stop` 会恢复 QoS**；如果中途 cancel（只置 finished），`stop` 不会执行，QoS 可能保持降级。

## 10. 复现与检查

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A6 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a6
```

| 文件 | 看什么 |
|---|---|
| `core.qos.jsonl` | `qos_flow_modify`、`5qi=9`、`old_5qi=79` |
| `app.ingest.jsonl` | `latency_ms≈82` |
| `v2x.cpm.jsonl` | `forged=true`、`degraded=true`、`lost=true` |
| `control.command.jsonl` | `decel_mps2=2.0`、`ttc=99` |
| `truth/events.jsonl` | `params.5qi_from/to` |
| `alerts.jsonl` | `qos_downgrade`、`ingest_latency_spike` |

## 11. 代码索引

| 作用 | 位置 |
|---|---|
| A6 类与 `start/tick/stop/truth` | `isacsim/attacks/campaigns.py:586`, `:598`, `:615`, `:636`, `:643` |
| `SMF.modify_qos` | `isacsim/layers/core/smf.py:100` |
| 平台 ingest | `isacsim/layers/platform/ingest.py:42` |
| 直接制动 | `isacsim/layers/control.py:144` |
| `qos_downgrade` | `isacsim/detect/reference.py:212` |
| `ingest_latency_spike` | `isacsim/detect/reference.py:218` |
| A6 真值链 | `isacsim/truth/model.py:39` |
| A6 profile | `isacsim/trace/profiles.py:282` |

## 12. 小结

```text
SMF 5QI 79→9（语义）
        │
platform_ingest_delay_ms=80 → app.ingest 82ms
        │
v2x.cpm(forged/degraded/lost) + inject_brake(2.0)
        │
        └─ qos_downgrade / ingest_latency_spike / brake_without_hazard
```

A6 是“核心网 QoS 状态 + 平台延迟 + 一条伪造 CPM + 制动”的组合；它没有真实队列调度，检测主要看 QoS 修改事件和 ingest 延迟。
