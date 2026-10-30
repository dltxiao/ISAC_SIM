# 04 Trace 采集与数据契约

> **当前状态（2026-10-30）**：`ctx.emit` 观察、A1/A5 StepFrame、core/app/defense 事件与 100 ms 聚合已实现；Hook before/after、origin、增量游标、trace.jsonl 未实现。
> 本文同时保留目标契约与已实现契约；未实现项已明确标注。完整差异见 [12 当前实现状态](12-当前实现状态.md)。

## 1. 采集点

Trace 不是“再写一遍仿真”，而是从现有代码的少数稳定接缝取数。

| 采集点 | 代码位置 | 拿什么 | 用途 |
|---|---|---|---|
| 记录出口 | `SimContext.emit()`（[context.py](../../isacsim/core/context.py)） | post-hook 的 `Record` | 观测事实、watch 帧、事件 |
| 注入前后 | `HookBus.apply()`（[context.py](../../isacsim/core/context.py)） | before/after/dropped | “攻击改了什么” |
| tick 末尾 | `Simulation.step_once()`（[engine.py](../../isacsim/core/engine.py)） | 车辆、V2X、控制、检测器 | 状态类 watch |
| 攻击生命周期 | `AttackScheduler` / `AttackInstance` | start/tick/stop、参数 | `atk:*`、检查点 |
| 真值 | `TruthStore` | `TruthEvent` | 可选 `truth:*` |
| 检测器 | `ReferenceDetector.snapshot()` | 最近告警 | `det:alerts` |

> 重要：记录出口是 **post-hook**。A1 的幽灵已经被追加；检测器看到的也是这一版。`HookProbe` 额外保留 before/after，才能展示“注入动作”。

## 2. 事件分类（当前实现）

| `kind` | 产生者 | 例子 | 是否关键事件 |
|---|---|---|---|
| `attack.start` | TraceService | A1/A5 启动 | 是 |
| `attack.end` | TraceService | t1 到达 | 是 |
| `ghost.added` | TraceService | RSU-01 +3 ghost | 是 |
| `control.command` | TraceService | TTC 1.481 制动 | 是 |
| `pc5.rise` | TraceService | offered/CBR 上升 | 是（阈值标记） |
| `vehicle.speed_drop` | TraceService | 速度 -0.5 m/s | 是 |
| `alert` | TraceService | `ghost_object`/`core` 规则 | 是 |
| `core.nas` / `core.session` / `core.qos` / `core.upf` / `core.handover` / `core.nef` | TraceService | 注册拒绝、PDU 失败、QoS 修改 | 是（100 ms 聚合） |
| `app.auth` / `app.ingest` / `app.subscription` / `app.control` / `app.audit` | TraceService | token/订阅/云控 | 是（100 ms 聚合） |
| `defense.action` | TraceService | AMF 限流、路由回切 | 是 |
| `record.forged_denm` / `record.clone` | TraceService | A1 DENM、A4 克隆 | 是 |

**规划中但未实现**：`session.start/end`、`attack.stage`、`hook.before/after/drop`、`watch.delta`、`trace.error`、`truth`。
当前 StepFrame 的 `reason` 承担 stage 语义，没有独立 `attack.stage` 事件。

## 3. 当前信封

当前 `TraceEvent` 实际字段：

```json
{
  "seq": 1842,
  "t": 3.10,
  "tick": 62,
  "kind": "core.nas",
  "summary": "registration_reject reject @ amf:AMF-01 (congestion)",
  "severity": "effect",
  "target_id": "core:amf",
  "attack_id": null,
  "data": {
    "stream": "core.nas",
    "source": "ue:bot-0001",
    "entity": "amf:AMF-01",
    "msg_type": "registration_reject",
    "result": "reject",
    "cause": "congestion",
    "latency_ms": 12.0,
    "count": 1
  }
}
```

字段约束：

- `seq`：会话内单调递增；事件和帧共用；
- `tick` / `t`：仿真步号/秒；
- `attack_id`：当前 core/app 事件通常为空；A1/A5 的攻击生命周期事件有值；
- `target_id`：可空；
- `data`：各 kind 自定义；core/app 事件带 `stream/source/entity/msg_type/result/cause/count`。

**当前没有** `run_id`、`schema`、`t_wall_ns`、`namespace`、`refs`、`parent_seq` 字段。规划中的统一信封见 §3.1。

### 3.1 规划信封（未实现）

```json
{
  "schema": "isacsim.trace/1",
  "run_id": "web-arterial-11-...",
  "seq": 1842,
  "tick": 62,
  "t": 3.10,
  "t_wall_ns": 1730000000000000000,
  "kind": "watch.delta",
  "source": "probe:record",
  "namespace": "observation",
  "attack_id": "ATK001",
  "stage_id": "S1",
  "target_id": "sensor:RSU-01.objects",
  "severity": "info",
  "summary": "RSU-01.objects +3 ghosts",
  "data": {},
  "refs": {"record_seq": [1801], "parent_seq": 1800}
}
```

## 4. 当前 `WatchFrame` 契约

```json
{
  "seq": 1842,
  "tick": 62,
  "t": 3.10,
  "target_id": "sensor:RSU-01.objects",
  "status": "changed",
  "summary": "6 objects / 3 ghosts",
  "values": {"rows": [], "object_count": 6, "ghost_count": 3, "frame_t": 3.1},
  "delta": {"added": [9000, 9001, 9002], "removed": [], "changed": []}
}
```

当前规则：

- `TraceStore.frames[target_id]` 只保留**最新一帧**，不是每 target 600 帧历史；
- 表 target 的 `values.rows` 有上限（sensors 80、fleet 40、core tables 40）；
- `delta` 没有 `mode` 字段；表差异按 key 计算 added/removed/changed；
- `status` 当前为 `new`/`changed`，没有 `same/stale/error`；
- 规划中的 `schema`、`run_id`、`baseline`、`refs` 未实现。

## 5. 当前 `StepFrame` 契约

```json
{
  "schema": "isacsim.step/1",
  "step_id": "ATK001:S1:2",
  "index": 2,
  "title": "目标列表被篡改",
  "reason": "S1",
  "from": {"seq": 1800, "tick": 60, "t": 3.00},
  "to": {"seq": 1842, "tick": 62, "t": 3.10},
  "attack_id": "ATK001",
  "profile_id": "a1",
  "stage_id": "S1",
  "narrative": "RSU-01 +3 ghost target(s)",
  "before": {"sensor:RSU-01.objects": {"object_count": 3}},
  "after": {"sensor:RSU-01.objects": {"object_count": 6}},
  "events": [1801, 1803, 1810],
  "alerts": []
}
```

实际字段：`schema`、`step_id`、`index`、`title`、`reason`、`from`、`to`、`attack_id`、`profile_id`、`stage_id`、`narrative`、`before`、`after`、`events`、`alerts`。
**没有** `effects`、`truth`、`skipped`；`last_step`/`steps` API 只返回摘要（`event_count`/`alert_count`），完整 before/after 仍在内存。

## 6. 游标与排序

- `seq` 是唯一排序键；`t/tick` 用于显示和 seek；
- 同一 tick 内按产生顺序：attack → hook → record（detector 在 record 内实时触发）→ tick 末尾 state；
- 检测器可能在记录之后几毫秒才触发；仍归到同一 `t`，`seq` 更大；
- 客户端用 `since=seq`/`Last-Event-ID` 重连；
- 服务端保留“最近 N 条 + 所有 StepFrame”；超出后 `dropped_before` 告知客户端；
- 回放时 `seq` 由重建器重新分配，保持单调；原始 `ts_ns` 保留在 `refs`。

## 7. 采样与合并

### 7.1 为什么必须采样

60 s 场景有 21 万条记录；如果每条都变成事件，前端和 SSE 都会被淹没。策略：

| 数据 | 策略 |
|---|---|
| `sensing.objects` | 每个传感器每帧最多 1 个 watch 帧；只发有变化的 |
| `v2x.cam` | 默认不逐条上屏；只用于车辆速度/位置的 state 采样 |
| `link.metrics` | 10 Hz 全量（很小） |
| `control.command` | 全量（事件少） |
| `v2x.denm` | 全量（事件少；可折叠） |
| `auth.*` | 事件级；只保留关键状态变化 |
| `graph.snapshot` | 默认不进 trace；回放时单独读 |
| `sensing.radar.*` | 默认只采目标车/目标 RSU；否则太多 |

### 7.2 合并规则

- 同一 target 在同一 tick 的多次更新 → 合并为最后一帧 + 累计 delta；
- `same` 帧不推 SSE；但保留在 ring buffer 的“最后已知值”；
- 表差异按 key 合并：同一 key 多次变化取最终 before/after；
- 关键事件不合并、不丢弃；
- 慢客户端：服务端只发最新 watch 帧，事件按 seq 补齐；`dropped_watch` 计数。

### 7.3 当前上限

| 对象 | 上限 | 超出 |
|---|---|---|
| 每 target 帧 | **只保留最新一帧** | 历史帧不保留；回放未实现 |
| 全局事件 | 3,000 | 淘汰最旧事件（含关键事件） |
| StepFrame | 50 | 淘汰最旧 |
| sensor rows | 80 | 截断（客户端再显示 40） |
| fleet rows | 40 | `truncated` |
| core/platform 表 | 40 | `total`/`truncated` |
| 单条 SSE | 无显式上限（全量 snapshot） | 未做裁剪 |

> 设计中的“每 target 600 帧、事件 10k、Step 200、SSE 256 KB”未实现。

## 8. 因果与 Xref（规划，未实现）

当前事件没有 `origin`/`caused_by`/`refs`；A4 直接 emit、A5 core/app 事件通常 `attack_id=null`。
当前可用的关联只有：

- `TraceEvent.attack_id`（攻击生命周期、A1 ghost/control 等）；
- `control.command` 的 `data.src`（如 `adas:<vid>`/`platform:V2X-01`）；
- A5 `core/app` 事件的 `data.stream/source/entity/msg_type`。

### 8.1 标记来源（规划）

- `HookProbe` 知道 hook 的 owner（attack_id）；
- 直接 `ctx.emit` 的攻击记录（A4、DENM 洪泛、id_clone）需要 `origin` 标记；
- `control.command` 由 ADAS 产生，`origin=system`，但 `caused_by` 指向触发它的目标/攻击；
- `detector` 告警 `caused_by` 指向它消费的记录 seq；
- `truth` 独立命名空间，不与 observation 混排。

> 当前 `HookBus` 只保存 callable，没有 owner 元数据；`ctx.emit()` 也没有 origin 参数。实施 P1 时建议给 hook 加 `owner=attack_id`（或在 `AttackInstance.hook()` 外包一层），给直接 emit 的攻击记录加 `origin`。否则 HookProbe 只能知道“这个流变了”，不知道“是哪个攻击改的”；A4/DENM 洪泛/`id_clone` 这类直接 emit 还会完全绕过 hook。

### 8.2 推荐的 `caused_by`

```text
hook.after(ghost added)
  → sensing.objects frame
    → control.command(ttc)
      → speed drop effect
        → ghost_object alert
```

`caused_by` 不是严格证明，只是“同一时间窗内最可能的父事件”；UI 标注“推断”，不要伪装成确定性因果。

### 8.3 多攻击

- 每个事件带 `attack_id`；
- 不能归因的共享资源（PC5、fleet）带 `attack_ids: []`；
- UI 默认按 focus 过滤；全局视图显示“多攻击”标签。

## 9. 存储

### 9.1 内存（当前）

```text
TraceService
├─ targets: dict[target_id, WatchTarget]
├─ frames: dict[target_id, WatchFrame]     # 只保留最新
├─ events: deque[TraceEvent]               # maxlen=3000
├─ steps: deque[StepFrame]                 # maxlen=50
├─ last_step: StepFrame | None
└─ providers: CoreProvider/PlatformProvider
```

单写者/多读者；`RLock` 保护整个服务。`snapshot()` 深拷贝 frames/events 给 API/SSE。

### 9.2 落盘（未实现）

```text
out/<run>/trace/
├─ meta.json          # 规划
├─ events.jsonl       # 规划
├─ watch.jsonl        # 规划
├─ steps.jsonl        # 规划
└─ summary.json       # 规划
```

当前只有原生 `out/<run>/*.jsonl`（由 `sinks` 产生），**没有 trace 专用落盘**。

### 9.3 现有 run 的兼容

- Web run 目前没有 `alerts.jsonl`（CLI 才有）；
- 回放/`ReplayAdapter` 尚未实现；当前只能看进程内快照。

## 10. 已知数据缺口

这些不是 trace 层能凭空补的，设计上要显式处理：

| 缺口 | 影响 | 当前处理 |
|---|---|---|
| `AttackInstance.snapshot()` 没有计算后的 `rsu_id/victim/ghost_count/extra_load` | `atk:A1` 卡片信息不全 | TraceService 读实例字段补齐 |
| `flags` 只标真值 victim | “受影响车辆”不完整 | 从 `control.command`/速度差派生；仍有粘滞 |
| `ctx.extras` 每 tick 重建 | A1 的 ADAS DENM/CAM 升频丢失 | trace 不依赖它；以记录/状态为准 |
| Web run 没有 `alerts.jsonl` | 回放没有告警 | 未实现回放；在线由 detector 记录 |
| 信道是全局 `net:pc5` | 不能做 per-RSU 信道 | 只做一个 PC5 target，不虚构 |
| `sensing.frame.object_count` 曾不含幽灵 | 帧计数与对象表不一致 | 已修：与 post-hook `sensing.objects` 一致 |
| `HookBus` 无 owner、`ctx.emit` 无 origin | diff 无法归因到攻击 | 加 owner/contextvar；直接 emit 打 origin |

## 11. 版本策略

- `isacsim.trace/1` 为初始版本；
- `WatchTarget` 增加可选字段不升 major；
- `WatchFrame.values` 的结构由 target schema 决定，target version 独立；
- 回放器支持 1 个旧版本；
- `meta.json` 记录 `generator_version`、`trace_version`、`profile_versions`。
