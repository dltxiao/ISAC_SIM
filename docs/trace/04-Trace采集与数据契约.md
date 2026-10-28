# 04 Trace 采集与数据契约

## 1. 采集点

Trace 不是“再写一遍仿真”，而是从现有代码的少数稳定接缝取数。

| 采集点 | 代码位置 | 拿什么 | 用途 |
|---|---|---|---|
| 记录出口 | `SimContext.emit()`（[context.py](../../isacsim/core/context.py:59)） | post-hook 的 `Record` | 观测事实、watch 帧、事件 |
| 注入前后 | `HookBus.apply()`（[context.py](../../isacsim/core/context.py:31)） | before/after/dropped | “攻击改了什么” |
| tick 末尾 | `Simulation.step_once()`（[engine.py](../../isacsim/core/engine.py:107)） | 车辆、V2X、控制、检测器 | 状态类 watch |
| 攻击生命周期 | `AttackScheduler` / `AttackInstance` | start/tick/stop、参数 | `atk:*`、检查点 |
| 真值 | `TruthStore` | `TruthEvent` | 可选 `truth:*` |
| 检测器 | `ReferenceDetector.snapshot()` | 最近告警 | `det:alerts` |

> 重要：记录出口是 **post-hook**。A1 的幽灵已经被追加；检测器看到的也是这一版。`HookProbe` 额外保留 before/after，才能展示“注入动作”。

## 2. 事件分类

| `kind` | 产生者 | 例子 | 是否关键事件 |
|---|---|---|---|
| `session.start` | TraceService | 会话开始 | 是 |
| `session.end` | TraceService | 正常/异常结束 | 是 |
| `attack.start` | AttackProbe | A1 选中 RSU-01 / eastbound.2 | 是 |
| `attack.stage` | AttackProbe/Profile | 进入“首次注入” | 是 |
| `attack.end` | AttackProbe | t1 到达 | 是 |
| `record` | RecordProbe | `sensing.objects` 被修改后 | 可采样 |
| `watch.delta` | Store | RSU-01.objects +3 | 是（有变化时） |
| `hook.before` / `hook.after` | HookProbe | 3→6 objects | 是（仅关注的 hook） |
| `hook.drop` | HookProbe | 回波被删除 | 是 |
| `control.command` | RecordProbe | TTC 1.481 制动 | 是 |
| `alert` | DetectorProbe | `ghost_object` | 是 |
| `effect` | Profile/StateProbe | 速度 14.86→12.32 | 是 |
| `step` | StepController | 第 3 步开始/结束 | 是 |
| `trace.error` | TraceService | probe 异常/丢弃 | 是 |
| `truth` | TruthProbe | A1 真值事件 | 可选 |

关键事件不允许在正常背压下丢弃；普通 `record` 可以采样。

## 3. 统一信封

所有 trace 数据带同一个信封，便于排序/重连/回放。

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

字段约束：

- `seq`：会话内单调递增；所有事件/帧共用；
- `tick`：仿真步号；`t`：仿真秒；
- `t_wall_ns`：真实墙钟，仅诊断；**不用于排序**；
- `namespace`：`observation` / `truth` / `system`；
- `attack_id` / `stage_id`：可空；
- `target_id`：可空（session 级事件）；
- `refs.parent_seq`：因果父事件；
- `data`：类型由 `kind` 决定。

## 4. `WatchFrame` 契约

```json
{
  "schema": "isacsim.watch/1",
  "run_id": "...",
  "seq": 1842,
  "tick": 62,
  "t": 3.10,
  "target_id": "sensor:RSU-01.objects",
  "target_version": 1,
  "status": "changed",
  "summary": {"object_count": 6, "ghost_count": 3},
  "values": {"rows": [], "frame_seq": 31},
  "delta": {"mode": "previous", "added": [9000, 9001, 9002],
            "removed": [], "changed": []},
  "refs": {"record_seqs": [1801, 1802]}
}
```

设计规则：

- 每个 target 每个更新周期最多一帧；同一 tick 的多次 emit 合并；
- `values` 只放前端需要的字段，不塞整条原始记录；
- `delta.mode` 默认 `previous`；切换 baseline 由查询参数控制，不重写历史；
- `refs` 指向原始事件/记录，用于“查看原始 JSON”；
- `status=same` 可被服务端合并，不必每条 SSE 都发。

## 5. `StepFrame` 契约

```json
{
  "schema": "isacsim.step/1",
  "step_id": "ATK001:S1",
  "index": 2,
  "title": "目标列表被篡改",
  "reason": "checkpoint",
  "from": {"seq": 1800, "tick": 60, "t": 3.00},
  "to": {"seq": 1842, "tick": 62, "t": 3.10},
  "attack_id": "ATK001",
  "stage_id": "S1",
  "narrative": "A1 向 RSU-01 的目标列表追加了 3 个静止目标",
  "before": {"sensor:RSU-01.objects": {"object_count": 3}},
  "after": {"sensor:RSU-01.objects": {"object_count": 6}},
  "events": [1801, 1803, 1810],
  "alerts": [],
  "effects": [{"veh:eastbound.2.state": {"speed": {"before": 14.86, "after": 14.42}}}],
  "truth": null
}
```

`StepFrame` 是单步 UI 的主数据；它引用事件，不重复整条 payload。`before/after` 只放差异摘要，详细值可通过 target 查询。

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

### 7.3 上限

| 对象 | 上限 | 超出 |
|---|---|---|
| 每 target 帧 | 600（60 s @10Hz） | 环形淘汰 |
| 全局事件 | 10,000 | 淘汰最旧的非关键事件 |
| StepFrame | 200 | 淘汰最旧 |
| 单帧 rows | 200 | 截断 + `truncated=true` |
| 单条 SSE | 256 KB | 截断并提示 |

## 8. 因果与 Xref

### 8.1 标记来源

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

### 9.1 内存

```text
TraceStore
├─ targets: dict[target_id, WatchTarget]
├─ last: dict[target_id, WatchFrame]
├─ frames: dict[target_id, deque[WatchFrame]]
├─ events: deque[TraceEvent]
├─ steps: deque[StepFrame]
└─ counters: seq, dropped, bytes
```

单写者/多读者；锁粒度按 store 整体即可（数据量小）。写操作只做浅拷贝/必要字段投影，避免深拷贝整条记录。

### 9.2 落盘（可选）

```text
out/<run>/trace/
├─ meta.json          # run_id, schema, profiles, caps
├─ events.jsonl       # TraceEvent
├─ watch.jsonl        # WatchFrame（可只存变化帧）
├─ steps.jsonl        # StepFrame
└─ summary.json       # 计数、丢弃、耗时
```

默认只在 `--trace` 或 Web 演示模式开启；批处理默认关闭。

### 9.3 现有 run 的兼容

- Web run 目前没有 `alerts.jsonl`（CLI 才有，[cli.py](../../isacsim/cli.py:66)）；
- 回放时若没有告警文件，`det:alerts` 标为不可用，不伪造；
- `graph.snapshot` 有 10 Hz 车辆位置，可用于回放地图（[edge.py](../../isacsim/layers/edge.py:12)）；
- 旧 run 没有 trace 文件时，用 `ReplayAdapter` 从原始流重建。

## 10. 已知数据缺口

这些不是 trace 层能凭空补的，设计上要显式处理：

| 缺口 | 影响 | 设计处理 |
|---|---|---|
| `AttackInstance.snapshot()` 没有计算后的 `rsu_id/victim/ghost_count/extra_load` | `atk:A1` 卡片信息不全 | AttackProbe 读实例字段/truth；或补 snapshot 字段 |
| `flags` 只标真值 victim | “受影响车辆”不完整 | 从 `control.command`/速度差派生 |
| `ctx.extras` 每 tick 重建 | A1 的 ADAS DENM/CAM 升频丢失 | trace 不依赖它；以记录/状态为准 |
| Web run 没有 `alerts.jsonl` | 回放没有告警 | 在线时由 DetectorProbe 记录；或后续落盘 |
| 信道是全局 `net:pc5` | 不能做 per-RSU 信道 | 只做一个 PC5 target，不虚构 |
| `sensing.frame.object_count` 不含幽灵 | 帧计数与对象表不一致 | 以 `sensing.objects` 为准；frame 标 `derived_count` |
| `HookBus` 无 owner、`ctx.emit` 无 origin | diff 无法归因到攻击 | 加 owner/contextvar；直接 emit 打 origin |

## 11. 版本策略

- `isacsim.trace/1` 为初始版本；
- `WatchTarget` 增加可选字段不升 major；
- `WatchFrame.values` 的结构由 target schema 决定，target version 独立；
- 回放器支持 1 个旧版本；
- `meta.json` 记录 `generator_version`、`trace_version`、`profile_versions`。
