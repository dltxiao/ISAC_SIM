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

`modify_qos` 在 scheduler 调用，事件 `core.qos` 由 SMF 直接 emit；QoS 状态写在
`PDUSession.qos_flows` 上，V2X 层每 tick 按该状态派生 **按 UE 的 CPM 接收丢包/时延**——
这是 A6 现在唯一“真后果”的来源（旧实现是伪造一条 `lost=true` 的 CPM）。

## 3. 选目标与参数

```python
def ready(ctx):
    if core is None or platform is None: return False, "core/platform not ready"
    if not self._candidates(ctx):        return False, "no PDU session"
    return True, ""
```

- 只选**已有 PDU 会话**的 UE，保证 `modify_qos` 有落点；
- 后到也能跑：`ready()` 不满足时保持排队，最多等 1 s（`READY_TIMEOUT_S`）；
  超时才 `abort()` 并写一条带 `observed.aborted/blocked_reason` 的真相——
  旧实现是 `finished=True` 静默退出，连真值都没有（审核 R1）；
- `spec.target` 命中候选时优先，并记 `truth.params.target_matched`。

| 量 | `a=0.2` | `a=0.8` | `a=1.0` |
|---|---:|---:|---:|
| 5QI | 79 → 9 | 79 → 9 | 79 → 9 |
| GFBR | 840 kbps | 360 kbps | 200 kbps |
| CPM 接收丢包率（派生） | 0.38 | 0.62 | 0.70 |
| CPM 额外时延 | 29.6 ms | 58.4 ms | 68.0 ms |
| 平台 ingest 延迟 | 72 ms | 168 ms | 200 ms |
| 制动 | 1.56 m/s² | 2.64 m/s² | 3.0 m/s² |

强度体现在**保证比特率被抢走多少**（以及由此派生的 CPM 接收质量），不再固定在 5QI 一个点上。
包络判据：`gfbr_to <= 400 kbps 且 ingest_delay >= 100 ms`。

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

它改 `PDUSession.qos_flows[1]` 和 `degraded` 集合；UPF 的队列不动，但 V2X 层会读这个
状态，给该 UE 的 CPM 接收加上丢包与额外时延（§4.3）。

### 4.2 平台延迟

```python
ctx.extras["platform_ingest_delay_ms"] = _level(amplitude, 40.0, 200.0)
```

`PlatformLayer.tick()` 的 ingest 会读这个值，`latency = 2 + 40..200 ms`；所有 CPM/DENM/SPaT
都受影响，不只 victim。

### 4.3 CPM 接收降级（由 QoS 状态派生）

V2X 层每 0.1 s 广播一次 CPM，并对每个有 PDU 会话的车发一条 `v2x.cpm.rx`：

```python
loss_p, extra_ms = qos_impairment(five_qi, gfbr_kbps, self.per)
ctx.emit("v2x.cpm.rx", sensor.id, f"veh:{vid}", {
    "msg_type": "cpm_rx", "sender": sensor.id, "receiver": vid,
    "5qi": five_qi, "gfbr_kbps": gfbr,
    "degraded": five_qi <= 9,
    "lost": bool(self.rng.random() < loss_p),
    "loss_p": loss_p, "delay_ms": delay + extra_ms,
})
```

- `qos_impairment()`（[v2x.py](../../isacsim/layers/v2x.py)）：
  79 档按信道 PER；≤9 档按 `0.30 + 0.50×(1−gfbr/1000)` 丢包、`20 + 60×(1−gfbr/1000)` ms 时延；
- 这是**真丢包**：`lost` 由该 UE 的 QoS 派生，不是攻击者写的；
- 基线车也会发 `v2x.cpm.rx`，所以“只有受害车有接收记录”不会变成标记。

### 4.4 制动

```python
control.inject_brake(ctx, self.veh_id, decel=self.brake_decel, duration=1.0)
```

`decel = 1.2 + 1.8a`；`reason=injected`、`ttc=99`、`src=attacker:ctl-*`。
这一腿仍是攻击者直接下发，不是“QoS 降级导致的远控延迟”，见 §10.3。

## 5. 下游消费

| 消费者 | 看到什么 |
|---|---|
| CoreProvider | `core:smf` 的 `degraded_sessions`、`core:sessions` 的 `five_qi=9` |
| V2X | 按 UE 的 `v2x.cpm.rx`：受害车丢包率 0.62、额外 58 ms |
| Platform | 所有 ingest 168 ms；`app.ingest` 延迟告警 |
| Control | 一条 `control.command` |
| Detector | `qos_downgrade`、`cpm_degraded`、`ingest_latency_spike`、`brake_without_hazard` |
| Trace | `core.qos`、`v2x.cpm.degraded`、`app.ingest`、`control.command` |

## 6. 真值

`CampaignA6.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:643)）实测：

```json
{
  "nodes": ["smf:SMF-01", "upf:UPF-01", "platform:V2X-01",
            "veh:southbound.0", "attacker:A6"],
  "domains": ["core", "app", "v2x", "control"],
  "params": {"5qi_from": 79, "5qi_to": 9, "gfbr_from": 1000, "gfbr_to": 360,
             "cpm_loss_p": 0.62, "cpm_extra_delay_ms": 58.4,
             "ingest_delay_ms": 168.0, "brake_decel": 2.64,
             "victim": "southbound.0", "target_matched": false},
  "envelope": {"in_envelope": true, "criterion": "gfbr <= 400kbps and ingest_delay >= 100ms"},
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

`causal_chain` 里的 `upf:queue_up` 仍没有对应代码：UPF 只改 route/flow 表，没有队列增长。
现在真实的传导是 `core.qos → PDUSession.qos_flows → v2x.cpm.rx（丢包/时延）`，
`control.command:late` 仍然是攻击者直接下发，不是 QoS 导致的延迟。

## 7. 检测侧看到了什么

| 规则 | 触发 | 实测 |
|---|---|---|
| `qos_downgrade` | `qos_flow_modify` 且 5qi<=9 | 1 条 |
| `cpm_degraded` | 某 UE 最近 24 帧里丢包 ≥14（≈58%）且 5QI≤9 | 2–3 条 |
| `ingest_latency_spike` | `app.ingest.latency_ms>=50` | 3 条 |
| `brake_without_hazard` | `ttc_s>=10` | 1 条 |

seed=14、a=0.8、3 s 攻击：

```text
qos_downgrade ×1, cpm_degraded ×2, ingest_latency_spike ×3, brake_without_hazard ×1
```

## 8. 外部检测视角：数据支撑、`/trace` 位置与 `out/` 落点

> 本节假设判定在仿真之外完成：只看导出的观测流与日志，不读 `truth/`、不读 `ctx.extras`。可观测面清单与路径/落盘规则见 [A1 §9.1、§9.4](A1-感知篡改诱导通信拥塞.md)。

### 8.1 判定 A6 需要的数据支撑

A6 的注入点是“切片/QoS + 平台延迟 + 一条伪造 CPM + 一次小制动”。QoS 只有语义状态（没有真实队列/调度），所以外部系统应以**QoS 修改事件**为主线，用平台延迟和制动做因果闭环。

| 观测面 | 需要捕获的字段 | 在判定中的作用 | 可依赖度 |
|---|---|---|---|
| `core.qos` | `msg_type`、`ue_id`、`pdu_session_id`、`5qi`、`old_5qi`、`gfbr_kbps`、`qfi` | `qos_flow_modify` 把 5QI 79→9、GFBR 1000→360；恢复时 9→79 | 协议证据（关键） |
| `v2x.cpm.rx` | `receiver`、`5qi`、`gfbr_kbps`、`lost`、`loss_p`、`delay_ms` | 受害车 CPM 接收丢包率 ≈0.62、额外 58 ms；其他车仍 79 档 | 物理证据（关键） |
| `app.ingest` | `latency_ms`、`msg_type`、`source` | 平台 ingest 168 ms（全局） | 物理证据 |
| `control.command` | `params.decel_mps2`、`params.ttc_s`、`src` | `ttc=99`、`decel=2.64`、`src=attacker:ctl-*` | 物理/协议证据 |
| `link.metrics` | `cbr`、`jam` | 不变 → 排除 A1/A2 | 反证 |
| `core.upf` | `route`、`msg_type` | 不变 → 排除 A7 | 反证 |

判定口径：

```text
P1 core.qos 出现 qos_flow_modify 且 5qi<=9（V2X 切片正常是 79）→ QoS 降级。
P2 同一会话 gfbr_kbps 从 1000 跌到 100 → 带宽承诺下降。
P3 同一 UE 的 v2x.cpm.rx 最近 24 帧里丢包 ≥14（≈58%，正常 79 档 ≈信道 PER 0.01）、
   delay_ms 高出基线 50 ms 以上。
P4 app.ingest.latency_ms >= 50（本例 168）→ 平台侧变慢。
P5 control.command 的 ttc_s>=10，且前序没有对应的 CPM/DENM 危险事件。
=> P1∧P3 判 A6（QoS 降级 + CPM 接收真的变差）；P4 是佐证；P5 用于归因。
```

边界：

- QoS 仍是语义状态：没有按 5QI 调度的 UPF 队列，`upf:queue_up` 这个真值环节不存在；
- CPM 降级是真的：`lost` 由该 UE 的 (5QI, GFBR) 派生，不是攻击者写的标记；
- 平台延迟是全局的，所有 CPM/DENM/SPaT 都慢 40–200 ms；
- `amplitude` 现在有实际作用：GFBR 1000→200、延迟 40→200 ms、制动 1.2→3.0 m/s²；
- 恢复时还会有一条 `qos_flow_modify`（9→79，恢复到**攻击前**的档位），不要当成第二次攻击。

### 8.2 对应前端 `/trace` 的位置

| 数据 | `/trace` 位置 | 看什么 |
|---|---|---|
| QoS 降级 | 右栏 **TRACE** 的 `core.qos` 事件 → **DETAIL** | `qos_flow_modify`、`5qi=9`、`old_5qi=79`（`gfbr_kbps` 不在事件白名单，去 `out/`） |
| SMF 状态 | **核心网** → `核心网 · Core` 卡 → 子标签「节点」 | SMF 节点变橙，悬停 reasons 含 `degraded QoS 1`；指标里没有 `degraded_sessions` |
| 会话表 | **核心网** → 子标签「会话」 | 受害 UE 行的 `five_qi=9`、`gfbr_kbps=100`；会话数 >40 时可能被截断 |
| CPM 接收降级 | 右栏 `v2x.cpm.degraded` 事件 | `receiver=<victim>`、`5qi=9`、`lost/loss_p/delay_ms` |
| 平台延迟 | **平台** → `平台 · V2X` 卡（概览）；右栏 `app.ingest` | `ingest_slow` 上升；`latency_ms≈168` |
| 制动 | **车辆** → `<victim> · state`；右栏 `control.command` | `decel=2.64`、`ttc=99`、`speed_drop` |
| 告警 | **总览** → `Detector · alerts`；**底栏时间轴** | `qos_downgrade`、`cpm_degraded`、`ingest_latency_spike`、`brake_without_hazard` |
| 阶段 | 顶栏 **下一步** 后的检查点横幅 | S1 5QI 79→9（core 层后）→ S2 CPM 降级 → S3 Ingest 延迟 → S4 云控 → S5 告警 |

不在 `/trace` 上的：CPM 的 `objects[]` 原文（卡片只给条数与标记）、UPF 队列（不存在）、5QI 的历史曲线。

### 8.3 对应后端 `out/` 的位置

| 数据 | 文件 | 过滤键 / 字段 | 备注 |
|---|---|---|---|
| QoS 降级/恢复 | `core.qos.jsonl` | `.payload.msg_type=="qos_flow_modify"`；看 `5qi`/`old_5qi`/`gfbr_kbps` | 两条：9 和 79 |
| CPM 接收降级 | `v2x.cpm.rx.jsonl` | `.payload.receiver=="<victim>"`、`.payload.5qi==9` | 按窗口算 `lost` 比例 |
| 平台延迟 | `app.ingest.jsonl` | `.payload.latency_ms>=50` | 168 |
| 制动 | `control.command.jsonl` | `.payload.params.decel_mps2==2.64`、`ttc_s==99` | |
| 真值（评估侧） | `truth/events.jsonl` | `.campaign=="A6"` | `params.5qi_from/5qi_to/gfbr_to/cpm_loss_p` |
| 告警 | `alerts.jsonl` | `qos_downgrade` / `cpm_degraded` / `ingest_latency_spike` / `brake_without_hazard` | 仅 CLI/batch |

```bash
cd out/doc_a6/arterial-14-*
jq -c 'select(.payload.msg_type=="qos_flow_modify")
       | {ue:.payload.ue_id, q5:.payload["5qi"], old:.payload["old_5qi"], gfbr:.payload.gfbr_kbps}' core.qos.jsonl
jq -c 'select(.payload.5qi==9) | .payload | {r:.receiver, lost, loss_p, delay_ms}' v2x.cpm.rx.jsonl | head -5
```

> A6 的“状态表”证据（`core:sessions` 的 `five_qi`）来自内存快照，`out/` 没有快照文件；离线复盘要用 `core.qos` 事件重放会话 QoS。

## 9. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | `modify_qos(9, 360)` 并 emit `core.qos`；平台延迟 168 |
| 0（同一拍） | `inject_brake(decel=2.64)` |
| +0.1 | 第一帧受害车 `v2x.cpm.rx` 标 `5qi=9`、按 0.62 概率丢包 |
| +0.1 | `app.ingest` 延迟 170 ms |
| +0.8 | `cpm_degraded` 攒够 8 帧 |
| `t1` | 恢复攻击前 5QI/GFBR；清除延迟；CPM 接收回到 79 档 |

## 10. 边界与差异

1. **QoS 仍是语义状态**：没有按 5QI 调度的 UPF 队列；真实传导停在
   `PDUSession.qos_flows → v2x.cpm.rx`（丢包/时延），再往上没有排队论。
2. **平台延迟是全局的**：所有 ingest 都慢 40–200 ms，不是只针对受害车。
3. **制动腿是直接下发**：`control.command` 来自 `inject_brake`，不是“QoS 降级导致的远控延迟”。
4. **`upf:queue_up` 不存在**：真值链里的这一环没有实现（`upf.flows` 只记 flow 表）。
5. **`amplitude` 现在有效**：GFBR 1000→(1000−800a)、延迟 (40+160a) ms、制动 1.2+1.8a m/s²，
   但 5QI 固定降到 9（BE 档），强度体现在 GBR 缺口。
6. **`stop` 恢复的是攻击前档位**（记住 `old_5qi/old_gfbr`），不是硬写 79；
   取消（`cancel()`）现在也会走 `stop(ctx)`，所以不会留下降级状态（旧的“cancel 不恢复”已修）。

## 11. 复现与检查

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A6 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a6
```

| 文件 | 看什么 |
|---|---|
| `core.qos.jsonl` | `qos_flow_modify`、`5qi=9`、`old_5qi=79` |
| `app.ingest.jsonl` | `latency_ms≈168` |
| `v2x.cpm.rx.jsonl` | `receiver=<victim>`、`5qi=9`、`lost` 比例 ≈0.62 |
| `control.command.jsonl` | `decel_mps2=2.64`、`ttc=99` |
| `truth/events.jsonl` | `params.5qi_to/gfbr_to/cpm_loss_p` |
| `alerts.jsonl` | `qos_downgrade`、`cpm_degraded`、`ingest_latency_spike` |

## 12. 代码索引

| 作用 | 位置 |
|---|---|
| A6 类与 `ready/start/tick/stop/truth` | [campaigns.py](../../isacsim/attacks/campaigns.py) `class CampaignA6` |
| `SMF.modify_qos` | [smf.py](../../isacsim/layers/core/smf.py) `modify_qos` |
| CPM 接收降级 | [v2x.py](../../isacsim/layers/v2x.py) `_deliver_cpm/qos_impairment` |
| 平台 ingest | [ingest.py](../../isacsim/layers/platform/ingest.py) |
| 直接制动 | [control.py](../../isacsim/layers/control.py) `inject_brake` |
| `qos_downgrade` | [reference.py](../../isacsim/detect/reference.py) `_on_core_qos` |
| `cpm_degraded` | [reference.py](../../isacsim/detect/reference.py) `_on_cpm_rx` |
| A6 profile | [profiles.py](../../isacsim/trace/profiles.py) `A6_PROFILE` |
| 回归测试 | [tests/test_a456.py](../../tests/test_a456.py) |

## 13. 小结

```text
SMF 5QI 79→9、GFBR 1000→(1000−800a)
        │
        ├─ v2x.cpm.rx：该 UE 丢包率 0.30+0.50·缺口、额外 20+60·缺口 ms
        │        └─ cpm_degraded（24 帧丢包 ≥14 且 5QI≤9）
        ├─ platform_ingest_delay_ms = 40+160a → app.ingest 42–202 ms
        │        └─ ingest_latency_spike
        └─ inject_brake(1.2+1.8a)
                 └─ brake_without_hazard
```

A6 现在有一条真因果腿（QoS → CPM 接收质量），另两条（平台延迟、制动）仍是攻击者
直接施加的组合动作。它没有按 5QI 调度的 UPF 队列，关税/带宽承诺只体现在 GFBR 字段与
由此派生的接收质量上。
