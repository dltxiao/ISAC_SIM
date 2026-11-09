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
      ├─ 选一个已有 PDU 会话的受害车
      ├─ extras["platform_ingest_delay_ms"] = 60..240
      └─ platform.submit_control(token, brake 2.0/1.0)   # token 为空则下一拍重试
  → 每 0.05 s CampaignA5.tick(ctx)
      ├─ 保持 platform_ingest_delay_ms
      ├─ n = Pacer.due(t) 条 core.amf.start_registration(imsi-…, retry=False)
      │     └→ CoreBus → AMF.handle → accept()：超容量则 registration_reject(congestion)
      └→ t0+25%：core.reattach(victim)，重注册在拥塞窗口内被拒
  → stop(ctx)
      ├─ 清合成 UE（AMF/UDM/SMF，含总线在途消息）
      └─ 清除 platform_ingest_delay_ms（队列排空后受害车自动恢复入网）
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

- 洪泛**走 AMF 的真实入口** `core.amf.start_registration()`：登记请求进 `CoreBus`，
  由 AMF 的队列/容量决定接纳还是 `registration_reject(cause=congestion)`；
- 受害车由 `core.reattach()` 摘网后重注册，被拒/恢复都由 AMF/SMF 状态机产生；
- `platform_ingest_delay_ms` 在 scheduler 写入，同一 tick 的 platform ingest 读到；
- `submit_control` 在平台 token 就绪后提交（`start` 不行就下一 tick 重试），
  intent 由 `control.tick()` 在 platform 之后消费。

## 3. 参数

```python
self.capacity_tps = core.amf.capacity                    # 场景标定 800/s
self.rate = self.capacity_tps * _level(amplitude, 0.8, 2.0)   # 到达速率
self.bot_ues = 50 + int(round(150 * amplitude))          # 合成 UE 数
```

| `a` | 到达 `rate` | 与 AMF 容量之比 | 每 0.05 s 发送 |
|---:|---:|---:|---:|
| 0.1 | 736/s | 0.92× | 37 |
| 0.5 | 1120/s | 1.40× | 56 |
| 0.8 | 1408/s | 1.76× | 70 |
| 1.0 | 1600/s | 2.00× | 80 |

- 速率用 `Pacer` 累计补发，平均速率等于 `rate`（旧的 `t - last < 0.1` 门控会把
  0.1 s 边界判负，实际只有标称的 1/4–1/6）；
- `truth.params.rate` 是**到达速率**，`actual_rate` 是攻击窗口内实际发出的条数/时长；
- 包络判据改成 `actual_rate >= AMF 容量`，即“至少真能打满 AMF”；
- 实测 a=0.8、3 s：发送 4084 条，实际 ≈1361/s，AMF 接纳 ≈2586、拒绝 ≈1508。

## 4. 注入点

### 4.1 AMF 过载（由真实排队推导）

```python
for _ in range(n):
    core.amf.start_registration(ctx, f"imsi-4600{seq % bot_ues:05d}", "GNB-01",
                                retry=False)
```

`CoreBus` → `AMF.handle()` → `NetworkFunction.accept(ctx)`：

- 队列按 `capacity·dt` 排空，接纳量超过容量就丢弃并回 `registration_reject`；
- AMF 的 `shed_at=0.3`（SMF 是 0.8）：入口拥塞到 30% 就开始卸；
- `queue_depth`、`latency_ms`、`overloaded` 全部由排队推出，
  `amf_overload`（enter/clear）也由 AMF 自己发，攻击者不再 `set_overload(True)`；
- 过载期间`queue_depth≈90/300`，`latency_ms≈113 ms`（`base + queue/capacity`）。

### 4.2 合成 UE

- `ue_id` 形如 `imsi-460000042`，在 `bot_ues` 个 ID 间循环（身份多样性 50–200）；
- 不带任何 `bot`/`forged` 标记：真攻击者不会打标，检测也不该依赖它；
- `retry=False`：被拒后不重试，否则攻击结束后还会继续灌；
- `stop()` 里 `core.purge_synthetic("imsi-")` + `schedule_purge(..., t+0.4)`
  清掉 AMF/UDM/SMF 三处的合成 UE（含总线里的在途消息）。

### 4.3 受害车重注册

```python
self._detached = core.reattach(ctx, self.veh_id)   # 拥塞形成后（t0+25%）
```

`CoreLayer.reattach()`：`amf.release()` 摘网 → 下一 tick 的 attach 循环重新
`start_registration()`。拥塞窗口内 AMF 直接拒绝，攻击结束后自动恢复：

```text
t=2.80 deregistration accept      （受害车被摘网）
t=2.85 registration_reject  cause=congestion  queue=89  lat=113ms
t=3.35/3.90/4.45/5.00 同样被拒（每 0.5 s 重试一次）
t=5.05 registration_accept         （洪泛结束，队列排空）
t=5.10 pdu_session_establish_accept（会话恢复）
```

攻击者不再自己 emit 一条 `pdu_session_establish_reject`。

### 4.4 平台延迟与云控

```python
ctx.extras["platform_ingest_delay_ms"] = _level(amplitude, 60.0, 240.0)
...
platform.submit_control(ctx, token_id=token, ...)   # token 为空就下一 tick 重试
```

- 平台 ingest 的真实延迟变成 `2 + 60..240 ms`；
- 云控走 `Dispatcher.submit()`，emit `app.control` 与 `app.audit`；
- `start` 时 token 还没 bootstrap 就挂起到下一 tick（旧实现是静默跳过，
  真值却仍声明 `control.command`，见审核 R1）；
- 控制 intent 由 `ControlLayer` 消费，发出 `control.command`（`ttc=99`）。
  注意“云控老化”仍未建模：延迟作用在 ingest，不在指令本身（§10.5）。

## 5. 下游消费

| 消费者 | 看到什么 | 结果 |
|---|---|---|
| CoreProvider | AMF 队列 89/300、latency 113 ms、overload enter/clear | `core:amf` 变红 |
| Platform | ingest 延迟 62–242 ms | `app.ingest` 出现 `latency_ms>=50` |
| Control | 云控 intent | 一条 `control.command`（ttc=99） |
| Detector | 注册到达速率 + 拒绝占比、ingest 延迟、无依据制动 | `nas_registration_flood`、`ingest_latency_spike`、`brake_without_hazard` |
| Trace | 100 ms 聚合的 `core.nas`、`core.session`、`app.*` | A5 S0–S5 |

## 6. 真值

`CampaignA5.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:421)）实测：

```json
{
  "nodes": ["amf:AMF-01", "smf:SMF-01", "platform:V2X-01",
            "veh:westbound.0", "attacker:A5"],
  "domains": ["uu", "core", "app", "task", "control"],
  "params": {"bot_ues": 170, "rate": 1408.0, "actual_rate": 1361.33,
             "capacity_tps": 800.0, "victim": "southbound.0",
             "victim_reattach": true, "target_matched": false},
  "envelope": {"in_envelope": true, "criterion": "actual_rate >= AMF capacity (800/s)"},
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

1. `rate` 是**到达速率**，`actual_rate` 是实测发射速率；两者差 3% 以内（旧的标称/实际
   能差 5 倍，见审核 R11）；
2. `stages` 的第三段仍只是叙述分界，不是运行期开关；
3. 真值链里 `control.command:delay` 名不副实：`control.command` 在提交当拍就发出
   （`ttc=99`），被延迟的是平台 ingest，不是这条指令；“老化”仍未实现。

## 7. 检测侧看到了什么

| 规则 | 触发 |
|---|---|
| `nas_registration_flood` | 1 s 内 ≥300 条 `registration_request`，且拒绝占比 ≥10%（不看 `bot`） |
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
| `app.ingest` | `latency_ms`、`msg_type`、`source`、`result` | 平台 ingest 62–242 ms（全局），与信令风暴同窗口 | 物理证据 |
| `app.control` | `token_id`、`vehicle_ids`、`command`、`params`、`result` | 单车辆云控制动，来自合法 tenant token | 协议证据 |
| `app.auth` / `app.audit` | `msg_type`、`token_id`、`actor`、`action` | token 校验通过、审计记录 `control:submit` | 协议证据 |
| `control.command` | `src`、`reason`、`params.ttc_s` | `src=platform:V2X-01`、`reason=cloud_control`、`ttc=99` | 物理/协议证据 |
| `link.metrics` | `cbr`、`jam` | 不直接受影响 → 排除 A1/A2 | 反证 |

判定口径：

```text
N1 单位时间注册请求显著高于基线（本例 1 s ≥300 条，实测约 1400/s），
   且 ue_id 的多样性/可预测性异常（本例 170 个 ID 循环，id 熵 ≈0.12）。
N2 同窗口 core.nas 拒绝率高（cause=congestion，本例 ≈37%），AMF queue_depth 上升、latency 上升。
N3 app.ingest.latency_ms 持续 >=50，与 N1 同窗口。
N4 control.command 的 ttc_s>=10、src=platform:*，但前序没有对应的 CPM/DENM 危险事件。
=> N1∧N3 判 A5；N2 是佐证；N4 是后果与放大器，不是必要条件。
```

边界：

- 数据里没有 `bot`/`forged` 标记：检测只看速率、拒绝率、身份重复度；
- `truth.params.rate` 是到达速率，`actual_rate` 是实测速率（两者相差 <5%）；
- 受害车的注册拒绝是 AMF 真产生的，攻击结束后会自动恢复入网与会话；
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
| 注册洪泛 | `core.nas.jsonl` | `.payload.msg_type=="registration_request"` / `registration_reject`、`.payload.queue_depth` | 按 1 s 窗口计数与拒绝占比 |
| AMF 过载 | `core.nas.jsonl` | `.payload.msg_type=="amf_overload"`（`enter`/`clear`） | 攻击起止两个点 |
| 会话拒绝 | `core.session.jsonl` | `.payload.msg_type=="pdu_session_establish_reject"` | SMF 容量先打满时出现；受害车自身是注册被拒（无会话） |
| 平台延迟 | `app.ingest.jsonl` | `.payload.latency_ms>=50` | 本例 202 |
| 云控 | `app.control.jsonl` | `.payload.command=="brake"`、`vehicle_ids` 含受害车 | token 是合法租户 token |
| 鉴权/审计 | `app.auth.jsonl` / `app.audit.jsonl` | `token_validate accept` / `action=="control:submit"` | 与 A9 的区别在 vehicle_ids 规模 |
| 制动 | `control.command.jsonl` | `.payload.reason=="cloud_control"` | `ttc_s=99` |
| 真值（评估侧） | `truth/events.jsonl` | `.campaign=="A5"` | `params.bot_ues/rate` |
| 告警 | `alerts.jsonl` | `nas_registration_flood` / `ingest_latency_spike` / `brake_without_hazard` | 仅 CLI/batch |

```bash
cd out/doc_a5/arterial-14-*
jq -c 'select(.payload.msg_type=="registration_reject") | .payload.cause' core.nas.jsonl | sort | uniq -c
jq -c 'select(.payload.msg_type=="amf_overload") | {t:.ts_ns, r:.payload.result}' core.nas.jsonl
jq -c 'select(.payload.msg_type=="pdu_session_establish_reject") | .payload.ue_id' core.session.jsonl | head
jq -c 'select(.payload.latency_ms>=50) | {lat:.payload.latency_ms, src:.payload.source}' app.ingest.jsonl | head -3
```

> A5 的“快照类”证据（AMF 健康、队列深度）只在 `/trace` 与内存里；`out/` 只有事件流，需要按 `ts_ns` 重放才能还原当时的健康度。

## 9. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | 开始灌注册（60–80 条/0.05 s）；平台延迟 60–240 ms；提交云控 intent |
| 0 | `control.tick()` 发出 `control.command`（ttc=99） |
| +0.05 | AMF 队列上升、`latency_ms` 跟随 `queue/capacity` 上升 |
| +0.15～0.3 | AMF 入口拥塞到 30% 开始卸：`registration_reject(congestion)`、`amf_overload enter` |
| +0.5 | 检测器满足 ≥300 条/s 且拒绝占比 ≥10%，报 `nas_registration_flood` |
| +0.25·duration | 受害车被 `reattach()` 摘网，重注册在拥塞窗口内被拒 |
| `t1` | `stop()` 清合成 UE、平台延迟；队列排空、`amf_overload clear`；受害车随后自动恢复入网 |

## 10. 边界与差异

1. **注册走 AMF，但不是协议栈级洪泛**：不建 NAS 连接、不做完整性保护，只走
   `start_registration → CoreBus → handle → accept` 的语义状态机。
2. **容量是场景标定值**：AMF 800/s、SMF 600/s、队列 300（`docs/09 §4.4` 说明为什么
   不用现网的 5000/s：单路口场景用不到，而且会把事件量放大到不可用）。
3. **受害车会话是真的没了**：`reattach()` 摘网后 SMF 会话被移除，攻击结束后重新建立；
   `core:sessions` 在攻击窗口内确实少一条（旧实现是“拒绝但不改会话表”）。
4. **平台延迟是全局的**：所有 ingest 都慢，不是只慢受害车相关消息（`extras` 是单键）。
5. **云控不“老化”**：`control.command` 在提交当拍立即发出；`ttc=99` 是攻击者填的值，
   被延迟的是 ingest 而不是这条指令。
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
| `core.nas.jsonl` | `registration_reject`、`cause=congestion`、`queue_depth` 递减 |
| `core.session.jsonl` | `pdu_session_establish_reject` |
| `app.ingest.jsonl` | `latency_ms` 62–242 之间 |
| `app.control.jsonl` | `vehicle_ids=[victim]`、`params.decel_mps2=2.0` |
| `control.command.jsonl` | `src=platform:V2X-01`、`ttc=99` |
| `truth/events.jsonl` | `params.rate/actual_rate/capacity_tps/victim_reattach` |
| `alerts.jsonl` | `nas_registration_flood`、`ingest_latency_spike` |

## 12. 代码索引

| 作用 | 位置 |
|---|---|
| A5 类与 `ready/start/tick/stop/truth` | [campaigns.py](../../isacsim/attacks/campaigns.py) `class CampaignA5` |
| 注册入口（队列/容量） | [amf.py](../../isacsim/layers/core/amf.py) `start_registration/handle` |
| NF 排队模型 | [nf.py](../../isacsim/layers/core/nf.py) `tick/accept/latency_ms` |
| 受害车摘网/清理 | [layer.py](../../isacsim/layers/core/layer.py) `reattach/purge_synthetic` |
| `nas_registration_flood` | [reference.py](../../isacsim/detect/reference.py) `_on_core_nas` |
| `ingest_latency_spike` | [reference.py](../../isacsim/detect/reference.py) `_on_app_ingest` |
| `brake_without_hazard` | [reference.py](../../isacsim/detect/reference.py) `_on_control` |
| A5 profile | [profiles.py](../../isacsim/trace/profiles.py) `A5_PROFILE` |
| 回归测试 | [tests/test_a456.py](../../tests/test_a456.py) |

## 13. 小结

```text
start_registration ×N（合成 UE，60–80 条/0.05 s，无 bot 标记）
        │
        ├─ AMF 队列 → accept() 丢包 → registration_reject(congestion)
        │                             └─ amf_overload enter/clear
        ├─ nas_registration_flood（速率 + 拒绝占比）
        └─ ingest_latency_spike（平台 ingest 60–240 ms）

core.reattach(victim) → 重注册 → 拥塞期被拒 → 攻击结束后自动恢复
platform.submit_control → control.command(ttc=99) → brake_without_hazard
```

A5 现在是一条**真因果**链：攻击者只做“灌注册 + 摘网”，过载、拒绝、会话丢失、
恢复全部由 AMF/SMF 的队列与状态机产生。它仍不是协议栈级（没有 NAS 完整性保护、
没有真实 UE 上下文），但不再是“攻击者手写拒绝记录”。
