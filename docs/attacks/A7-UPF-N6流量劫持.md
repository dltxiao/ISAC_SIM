# A7 UPF/N6 流量劫持：代码走读与实现解析

> 代码基线：`feature/trace-watch`（v0.3.0 + A1–A10 TraceProfile），2026-11-03。
> 共享机制见 [A1 §2–§4](A1-感知篡改诱导通信拥塞.md)；本文只写 A7 的差异。
> 目标：解释“UPF 路由改到 EVIL → 伪造 CPM 进平台 → 云控制动”的代码路径，并明确它不是真实的 N6 抓包/转发。

## 1. 一句话概括

A7 做三件事：

1. `core.upf.set_route("dn:EVIL-01")`，把所有 flow 的 route 改成 EVIL；
2. 每 0.5 s 构造一条 `v2x.cpm`，直接调用 `platform.ingest.ingest(..., provenance="dn:EVIL-01")`；
3. 每 0.5 s 调 `control.inject_brake(decel=4.0, duration=1.0)`。

它没有真实抓包、没有改 N6 转发面、没有让平台主动拉取被劫持流量；`v2x.cpm` 是攻击者直接写进记录和平台 ingest 的。

## 2. 端到端调用链

```text
POST /api/attacks {type:"A7", amplitude, duration, start}
  → CampaignA7.start(ctx)                 campaigns.py:454
      ├─ 需要 core + platform
      ├─ pick_vehicle(seed,"a7")
      └─ core.upf.set_route("dn:EVIL-01")
  → CampaignA7.tick(ctx)                  campaigns.py:466
      ├─ 第一次 tick：_inject(ctx)
      └─ 每 0.5 s：_inject(ctx)
  → _inject(ctx)                          campaigns.py:476
      ├─ emit v2x.cpm(forged=true)
      ├─ platform.ingest.ingest(provenance="dn:EVIL-01")
      └─ control.inject_brake(4.0, 1.0)
  → stop(ctx)                             campaigns.py:495
      └─ core.upf.restore_route("dn:DN-01")
```

层序：

```text
scheduler → sensing → v2x → uu → core → auth → platform → control
```

A7 的 `_inject` 在 scheduler 执行，所以它写出的 `platform.ingest` 发生在 `platform.tick()` 之前；`control.command` 也在 scheduler 发出，不依赖后面的 control.tick。

## 3. 选目标与参数

```python
veh = pick_vehicle(ctx, self.seed, "a7")
core.upf.set_route(ctx, "dn:EVIL-01")
```

- victim 与 router 无关，只用于制动；
- `amplitude` 不影响路由、CPM 或制动；A7 的参数维度基本固定；
- `spec.target` 不生效；
- 没有检查 victim 是否在 core 里有会话。

| 量 | 值 |
|---|---:|
| 路由 | `dn:DN-01` → `dn:EVIL-01` |
| 注入周期 | 0.5 s |
| 制动 | 4.0 m/s²，1.0 s |
| 伪造 CPM 来源 | `dn:EVIL-01` |

## 4. 注入点

### 4.1 UPF 路由改向

`UPF.set_route()`（[upf.py](../../isacsim/layers/core/upf.py:20)）：

```python
self.route_override = route
for flow in self.flows.values():
    flow.route = route
ctx.emit("core.upf", self.entity, self.entity, {
    "msg_type": "route_change",
    "route": route,
    "flows": len(self.flows),
    "result": "applied",
})
```

- 改的是所有已安装 flow 的 `route` 字段；
- `core:flows` 表会显示 EVIL；
- 没有真实流量转发，也没有 N6 抓包。

### 4.2 伪造 CPM 进平台

```python
cpm = {
    "msg_type": "cpm", "sender": "ATK-A7", "object_count": 1,
    "objects": [{"object_id": 7001, "cls": "obstacle",
                 "x": 200.0, "y": 0.0}],
    "forged": True,
}
ctx.emit("v2x.cpm", "ATK-A7", "attacker:A7", cpm)
platform.ingest.ingest(
    ctx, {"stream": "v2x.cpm", "payload": cpm},
    token_id=platform.service_token_id or "svc-rsu",
    tenant=platform.tenant, provenance="dn:EVIL-01",
)
```

- `ctx.emit` 让 `v2x.cpm` 记录和检测器看到它；
- `platform.ingest.ingest` 直接写 `app.ingest`，`provenance=dn:EVIL-01`；
- 这条 CPM 不在 `v2x_outbox` 里，所以平台不会把它当普通 V2X 消息再处理一遍；
- `source=ATK-A7`，`schema_ok=true`，`result=accept`，因此不能靠 schema 拒绝它。

### 4.3 云控制动

```python
control.inject_brake(ctx, self.veh_id, decel=4.0, duration=1.0)
```

`reason=injected`、`ttc=99`、`src=attacker:ctl-*`；每 0.5 s 重复一次，所以一个 3 s 攻击会产生多条 `control.command`（seed=14 实测 7 条）。

## 5. 下游消费

| 消费者 | 看到什么 |
|---|---|
| CoreProvider | `core:upf.route_override=dn:EVIL-01`、flow route |
| Platform | `app.ingest.provenance=dn:EVIL-01`、`schema_ok=true` |
| Control | 周期性 `control.command`（ttc=99） |
| Detector | `upf_route_drift`、`ingest_provenance_mismatch`、`brake_without_hazard` |
| Trace | `core.upf`、`v2x.cpm.forged`、`app.ingest`、`control.command` |

## 6. 真值

`CampaignA7.truth()`（[campaigns.py](../../isacsim/attacks/campaigns.py:501)）实测：

```json
{
  "nodes": ["upf:UPF-01", "platform:V2X-01",
            "veh:eastbound.1", "attacker:A7"],
  "domains": ["core", "app", "v2x", "control"],
  "params": {"route": "dn:EVIL-01", "victim": "eastbound.1"},
  "envelope": {"in_envelope": true, "criterion": "route redirect"},
  "stages": [
    {"name": "UPF 路由改向", "t0": 2.0, "t1": 3.0},
    {"name": "伪造 CPM 注入平台", "t0": 3.0, "t1": 5.0}
  ],
  "causal_chain": [
    "core.upf:route_change",
    "v2x.cpm:forge",
    "app.ingest:provenance_mismatch",
    "control.command:inject"
  ]
}
```

## 7. 检测侧看到了什么

| 规则 | 触发 |
|---|---|
| `upf_route_drift` | route_change 且 route != `dn:DN-01` |
| `ingest_provenance_mismatch` | provenance 不是 `dn:DN-01` |
| `brake_without_hazard` | ttc_s >= 10 |
| `ingest_latency_spike` | 只有在别的攻击/全局延迟存在时才出现 |

seed=14、a=0.8、3 s 攻击实测：

```text
upf_route_drift ×1, ingest_provenance_mismatch ×3,
brake_without_hazard ×3
```

## 8. 时序

| 相对 `t0` | 事件 |
|---:|---|
| 0 | 路由改为 EVIL，`core.upf` 事件 |
| +0.05 | 第一次 `_inject`：伪 CPM + 平台 ingest + 制动 |
| +0.05 | `app.ingest` provenance 不符；`brake_without_hazard` |
| +0.5 | 第二轮 `_inject` |
| `t1` | `restore_route` 回 `dn:DN-01` |

## 9. 边界与差异

1. **不是真实 N6 劫持**：只改 flow 的 `route` 字段，没有抓包/转发。
2. **伪 CPM 是直接注入**：不在 `v2x_outbox`，平台不会按普通 CPM 处理。
3. **`schema_ok=true`**：伪 CPM 结构合法，不能靠 schema 拒绝。
4. **制动每 0.5 s 重复**：不是一次；短场景就会产生多条 `control.command`。
5. **`amplitude` 无效**：A7 的强度维度固定。
6. **`stop()` 会恢复路由**；如果 cancel，不会执行 stop，路由可能停在 EVIL。

## 10. 复现与检查

```bash
.venv/bin/python -m isacsim run \
  --seed 14 -a A7 --duration 8 --attack-duration 3 \
  --attack-start 2 --amplitude 0.8 --out out/doc_a7
```

| 文件 | 看什么 |
|---|---|
| `core.upf.jsonl` | `route_change`、`route=dn:EVIL-01` |
| `v2x.cpm.jsonl` | `forged=true`、`sender=ATK-A7` |
| `app.ingest.jsonl` | `provenance=dn:EVIL-01`、`schema_ok=true` |
| `control.command.jsonl` | 多条 `reason=injected`、`decel=4.0` |
| `truth/events.jsonl` | `params.route/victim` |
| `alerts.jsonl` | `upf_route_drift`、`ingest_provenance_mismatch` |

## 11. 代码索引

| 作用 | 位置 |
|---|---|
| A7 类与 `start/tick/_inject/stop/truth` | `isacsim/attacks/campaigns.py:442`, `:454`, `:466`, `:476`, `:495`, `:501` |
| `UPF.set_route` | `isacsim/layers/core/upf.py:20` |
| 平台 ingest | `isacsim/layers/platform/ingest.py:42` |
| `upf_route_drift` | `isacsim/detect/reference.py:207` |
| `ingest_provenance_mismatch` | `isacsim/detect/reference.py:218` |
| A7 真值链 | `isacsim/truth/model.py:36` |
| A7 profile | `isacsim/trace/profiles.py:305` |

## 12. 小结

```text
UPF.route_override=dn:EVIL-01
        │
每 0.5 s：
  v2x.cpm(forged) + platform.ingest(provenance=EVIL)
  control.inject_brake(4.0)
        │
        ├─ upf_route_drift
        ├─ ingest_provenance_mismatch
        └─ brake_without_hazard
```

A7 的“劫持”是语义层的路由漂移 + 直接伪造 CPM 进平台；它没有真实 N6 抓包/转发，也没有篡改平台 token。
