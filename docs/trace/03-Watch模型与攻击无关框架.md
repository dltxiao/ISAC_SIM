# 03 Watch 模型与攻击无关框架

> **当前状态（2026-11-02）**：`WatchTarget/WatchFrame/TraceEvent` 的概念已落地；`WatchProvider` 已用于 Core/Platform；`TraceProfile` 当前是 Python `StageSpec`（kind/predicate/pause_at），A1–A10 已接入。
> `TraceProfile.targets/narrative`、声明式 DSL、truth 仍为设计。差异见 [12 当前实现状态](12-当前实现状态.md)。

## 1. 为什么需要一层 Watch 模型

前端如果直接订阅 `sensing.objects` / `v2x.cam` / `link.metrics`，会遇到四个问题：

1. **流太多**：60 s 场景 30+ 条流（v0.2 基线 21 万条记录）；全量推给浏览器不现实；
2. **结构不一**：`objects` 是数组、`control.command` 是事件、`cbr` 是标量；
3. **语义缺失**：原始记录不知道“这是 A1 的目标 RSU”“这是受害车”；
4. **攻击耦合**：前端如果写死 A1 的字段，A2 又要重写。

因此引入 **Watch 模型**：后端把原始数据投影成“带类型、带标识、带差异”的观察单元；前端只渲染这些单元。

## 2. 核心类型

### 2.1 `WatchTarget`

一个被追踪的目标。它不是“变量”，而是“一个可观察的状态单元”。

```json
{
  "id": "sensor:RSU-01.objects",
  "kind": "table",
  "group": "Sensors",
  "label": "RSU-01 · sensing.objects",
  "description": "RSU-01 每帧的目标列表；A1 会在这里追加 ghost 目标",
  "source": {
    "type": "record",
    "stream": "sensing.objects",
    "filter": {"sensor_id": "RSU-01"}
  },
  "rate_hz": 10,
  "key_field": "object_id",
  "fields": [
    {"name": "object_id", "type": "int", "width": 8},
    {"name": "cls", "type": "enum", "width": 10},
    {"name": "x", "type": "float", "unit": "m", "precision": 1},
    {"name": "y", "type": "float", "unit": "m", "precision": 1},
    {"name": "vx", "type": "float", "unit": "m/s", "precision": 1},
    {"name": "entity", "type": "string", "role": "origin"},
    {"name": "ghost", "type": "bool", "role": "flag", "derived": true}
  ],
  "retention": {"frames": 600, "events": 200},
  "render": {"default_open": true, "max_rows": 20}
}
```

设计要点：

- `id` 稳定、可读、可用于 URL/收藏；
- `kind` 决定前端渲染器：`scalar` / `struct` / `table` / `event` / `series`；
- `source` 描述数据从哪来，前端不关心；
- `key_field` 用于表差异（added/removed/changed）；
- `fields` 带类型/单位/精度/角色，前端不硬编码；
- `derived` 表示由 profile/适配器计算（例如 `ghost = entity.startswith("ghost:")`）；
- `retention` 控制内存；
- `render` 是建议，不是硬编码。

### 2.2 `WatchField`

| 类型 | 渲染 | 例子 |
|---|---|---|
| `int` / `float` | 等宽数字，支持 hex/dec | `object_id`, `cbr` |
| `bool` | `true/false` 或标签 | `forged`, `cloned` |
| `enum` | 带颜色的标签 | `cls=obstacle` |
| `string` | 文本，可复制 | `entity=ghost:9000` |
| `timestamp` | `t=3.100` / `+0.05` | `first_seen` |
| `json` | 折叠的原始值 | `params` |
| `series` | sparkline | `cbr` 最近 3 s |

字段可以带 `role`：

- `identity`：用于行 key；
- `origin`：`veh:` / `ghost:` / `obj:` / `attacker:`；
- `flag`：攻击/异常标记；
- `metric`：可画 sparkline；
- `cause`：因果链接；
- `hidden`：默认不显示。

### 2.3 `WatchFrame`

某一时刻某个目标的值与差异。

```json
{
  "seq": 1842,
  "tick": 62,
  "t": 3.10,
  "target_id": "sensor:RSU-01.objects",
  "status": "changed",
  "summary": {"object_count": 6, "ghost_count": 3},
  "values": {
    "frame_seq": 31,
    "rows": [
      {"object_id": 1, "cls": "car", "x": 205.0, "y": -35.1, "entity": "veh:..."},
      {"object_id": 9000, "cls": "car", "x": 41.5, "y": -8.4, "entity": "ghost:9000", "ghost": true}
    ]
  },
  "delta": {
    "mode": "previous",
    "added": [9000, 9001, 9002],
    "removed": [],
    "changed": []
  },
  "refs": {"record_seq": [1801, 1802], "event_seq": [1803]}
}
```

`status`：

- `new`：目标第一次出现；
- `changed`：与上一帧不同；
- `same`：值未变（可合并，不必每帧上屏）；
- `stale`：超过 N 个周期未更新；
- `error`：采集失败。

### 2.4 `WatchDelta`

| 模式 | 含义 | 用途 |
|---|---|---|
| `previous` | 与上一帧比 | 实时变化高亮 |
| `baseline` | 与攻击前/用户选定快照比 | 讲“从正常到异常” |
| `none` | 不计算 | 大表/低开销 |

表的差异按 `key_field`：

- `added`：新 key；
- `removed`：消失的 key；
- `changed`：同 key 的字段变化，带 `before/after`；
- `reordered`：可选，默认忽略。

## 3. `WatchRegistry`

> 当前没有独立 `WatchRegistry` 类；全局 target 在 `TraceService._build_global_targets()`，Core/Platform 在 `providers/`。

### 3.1 全局目标（无攻击也注册）

当前实现：

| ID | 类型 | 来源 | 说明 |
|---|---|---|---|
| `scene:summary` | struct | `Snapshot` | 车辆数、攻击数、告警数、仿真时间 |
| `sensor:<id>.objects` | table | `sensing.last_objects` | 每个传感器一个；当前没有 `sensor:<id>.frame` |
| `veh:fleet` | table | `ctx.vehicles` | 车队总表；按 affected/ID 排序 |
| `veh:<victim>.state` | struct | 攻击 victim | 攻击启动后动态注册 |
| `net:pc5` | struct | `V2XLayer` | offered/CBR/PER/delay/DCC/jam |
| `det:alerts` | event | `detector.alerts` | 最近告警 |
| `defense:actions` | event | `defense.action` | 防御动作 |
| `core:summary` / `core:<nf>` / `core:ues/sessions/flows` | struct/table | `sim.core.snapshot()` | CoreProvider |
| `platform:summary/tokens/subscriptions` | struct/table | `sim.platform.snapshot()` | PlatformProvider |

规划中：`sensor:<id>.frame`、`graph:scene`、`truth:*`。这些不在当前注册表里。

### 3.2 攻击目标（由 profile 注册）

当前实现：

| ID | 类型 | 来源 | 说明 |
|---|---|---|---|
| `atk:<attack_id>` | struct | `AttackInstance` | 类型、t0/t1、参数、目标 |
| `veh:<victim>.state` | struct | `ctx.vehicles` | victim 状态 |

规划中：`atk:<attack_id>.stages`、`atk:<attack_id>.effects`、`truth:<event_id>`。当前 StepFrame 在 `steps` 里，不另建 stages 表。

### 3.3 命名规范

```text
<domain>:<entity>.<aspect>
```

- `domain`：`scene` / `sensor` / `veh` / `net` / `det` / `atk` / `truth` / `core` / `platform` / `defense`；
- `entity`：场景内稳定 ID（RSU-01、eastbound.2、ATK001、amf:AMF-01）；
- `aspect`：`objects` / `state` / `frame` / `stages` / `sessions` / `flows` / `tokens` / `subscriptions` / `alerts`；
- 避免在 ID 里放易变的时间/序号。

## 4. `TraceProfile`：攻击无关的接入点

### 4.1 当前接口（Python，已实现）

```python
@dataclass(frozen=True)
class StageSpec:
    id: str                       # S0/S1/.../END
    title: str
    kinds: tuple[str, ...]        # 匹配的 TraceEvent.kind
    pause_at: str = "after_tick"  # after_scheduler / after_layer:core|platform / after_tick
    predicate: Callable[[dict], bool] | None = None
    wait_for: tuple[str, ...] = ()
    wait_ref: str | None = None
    wait_s: float = 0.0

@dataclass(frozen=True)
class TraceProfile:
    id: str
    attack_types: tuple[str, ...]
    stages: tuple[StageSpec, ...]
```

当前只有 `A1_PROFILE`、`A5_PROFILE` 和空 `DEFAULT_PROFILE`。`StageSpec` 是 Python 声明，不是前端 DSL。

规划中（未实现）：

```python
class TraceProfile:
    attack_type: str
    def targets(self, ctx, inst) -> list[WatchTarget]: ...
    def checkpoints(self, ctx, inst) -> list[Checkpoint]: ...
    def annotate(self, event, ctx, inst) -> TraceEvent: ...
    def narrative(self, stage_id, values) -> str: ...
```

约束：

- profile **不执行攻击**，只描述“看什么/何时停/怎么讲”；
- profile 可以读 `inst` 的公开字段，不直接改仿真；
- 未注册的攻击当前不会产生专用检查点（只有 `attack.start/end` 等通用事件）；
- profile 的变更不应要求前端发版。

### 4.2 检查点 DSL（规划，未实现）

```json
{
  "id": "S1",
  "title": "目标列表被篡改",
  "when": {"type": "first_record", "stream": "sensing.objects",
           "where": {"sensor_id": "$target_rsu"}, "predicate": "ghost_added"},
  "watch": ["sensor:$target_rsu.objects"],
  "narrative": "A1 向 {rsu} 的目标列表追加了 {ghost_count} 个静止目标"
}
```

支持的类型见 [05 单步断点与时间模型](05-单步断点与时间模型.md)。profile 只产出声明，不执行 Python 表达式。

## 5. 攻击如何接入（框架价值）

### 5.1 A1（已实现）

- 检查点：S1 目标列表 → S2 首次 TTC 制动 → S3 CBR 上升 → S4 减速 → S5 告警 → END；
- 没有 S0；第一步 S1 已包含注入后的状态；
- 讲解重点：目标 RSU vs 其他 RSU 的对照。

### 5.2 A2（已实现）

- 额外证据：目标 RSU 的 `objects`（stop_sign）、`control.command`（injected）、`pc5.rise`、`vehicle.speed_drop`；
- 检查点：S0 启动 → S1 停止牌 → S2 注入制动 → S3 链路干扰 → S4 急刹 → S5 告警 → END；
- S2 之后的链路/减速阶段用 `wait_for` 保证展示顺序。

### 5.3 A3（已实现）

- 额外事件：`radar.echo`、`radar.track`、`control.command`；
- 检查点：S0 启动 → S1 回波偏移 → S2 点迹偏移 → S3 60% 处小指令 → S4 轻微减速 → S5 告警 → END；
- 雷达事件按受害者车每 tick 采样，不代表检测阈值；偏置随时间增长，暂不做 series 图。

### 5.4 A4（已实现）

- 额外事件：`record.clone`（auth.cert/auth.fingerprint）、`v2x.cam.clone`、`alert`；
- 检查点：S0 启动 → S1 克隆标识 → S2 复制指纹 → S3 伪 CAM → S4 位置跳变告警 → END；
- A4 的 `victim` 现在也会注册 `veh:<victim>.state` target，并参与事件归因。

### 5.5 A5–A10（已实现）

A5 保持原有 S0–S5；A6–A10 的阶段与暂停边界：

| 攻击 | 检查点 | 关键暂停边界 |
|---|---|---|
| A5 | S0 启动 → S1 AMF → S2 PDU → S3 平台 → S4 远控 → S5 告警 → END | scheduler/core/platform/tick |
| A6 | S0 启动 → S1 5QI 降级 → S2 CPM 降级 → S3 Ingest 延迟 → S4 云控 → S5 告警 → END | scheduler/core/platform/tick |
| A7 | S0 启动 → S1 UPF 路由改向 → S2 伪造 CPM → S3 云控 → S4 告警 → END | scheduler/core/platform/tick |
| A8 | S0 启动 → S1 NEF 退订 → S2 平台退订 → S3 漏事件 → S4 云控 → S5 告警 → END | scheduler/core/platform/tick |
| A9 | S0 启动 → S1 Token 复用 → S2 批量云控 → S3 控制到车 → S4 多车减速 → S5 告警 → END | scheduler/platform/tick |
| A10 | S0 启动 → S1 雷达渐偏 → S2 轻度信令 → S3 审计抑制 → S4 小幅制动 → S5 告警 → END | scheduler/core/platform/tick |

A6/A7/A8/A9/A10 的关键证据来自 `core.*`、`app.*`（含新增的 `v2x.cpm.forged`）；全局 target 仍由 provider 注册，profile 不拥有全局 target。

### 5.6 新增一个攻击的接入步骤（当前方式）

以 A2 为例：

1. 在 `isacsim/trace/profiles.py` 增加 `StageSpec` 列表；
2. 用 `kinds` + 可选 `predicate` 匹配现有 `TraceEvent`；
3. 设置 `pause_at`（`after_scheduler` / `after_layer:core|platform` / `after_tick`）；
4. 在 `PROFILES` 注册（当前覆盖 A1–A10）；
5. 跑单步/事件测试；
6. 前端不做任何改动。

> `profiles/a2.py`、`targets()`、声明式 DSL 仍是后续规划，不是当前文件布局。
> 如果某个攻击没有显式阶段事件，优先补只读事件投影；不要为了 trace 去改攻击逻辑。

## 6. 默认 profile 与“无攻击”

- 启动会话即注册全局 target（含 Core/Platform provider）；
- 没有攻击时，`atk:*` 为空，页面显示“当前无攻击”，但仍展示传感器/车辆/PC5/DET/Core/Platform；
- 未注册攻击当前使用空 `DEFAULT_PROFILE`：不会产生专用检查点，只有 MAX 兜底；
- 规划中的 `DefaultProfile`“首次变化/首次告警/结束”尚未实现。

## 7. 值语义与边界

### 7.1 观察值 vs 真值

- `sensor:*`、`veh:*`、`net:*`、`det:*`、`core:*`、`platform:*` 来自观测/仿真状态；
- 当前 trace **不注册 `truth:*`**，也不读 TruthStore；
- 检测器不读 trace/truth；测试要断言这一点；
- 规划中的 truth 演示命名空间未实现。

### 7.2 派生字段

- `ghost` = `entity.startswith("ghost:")`；
- `affected` 当前仍由 `control.command` + flags 推导，存在“普通 ADAS 也置位、攻击结束后粘滞”的已知问题；
- `delta` 由 store 计算，不由前端算；
- 派生字段必须在 metadata 里标 `derived`，避免误解为原始字段。

### 7.3 缺失值

- `null` 表示“该帧没有这个字段”，不是 0；
- `stale` 表示“目标存在但本帧未更新”；
- `error` 表示“采集失败”，要显示原因；
- 前端不得用 0 填补缺失值。

## 8. 版本与兼容（规划）

当前实现**没有** `schema_version`/`TraceEvent.version`，也没有 `ReplayAdapter`/profile 头。已实现的是向后兼容的字段追加（如 `profile_id`、`providers`、`last_layer`）。

- 规划：`WatchTarget.schema_version` / `TraceEvent.version`；
- 新增字段向后兼容；删除/改义升 major；
- 前端遇到未知 `kind` 降级为 JSON 折叠；
- 规划：旧 run 回放缺少 trace 文件时，用 `ReplayAdapter` 从原始流重建；
- 规划：profile 版本写入 trace 头部。

## 9. 一个最小的 A1 target 集合

```text
scene:summary
sensor:RSU-01.objects   ← 目标 RSU（高亮）
sensor:RSU-02.objects
sensor:RSU-03.objects
sensor:GNB-01.objects
veh:eastbound.2.state   ← 真值 victim
veh:fleet               ← 只显示受影响
net:pc5
det:alerts
atk:ATK001
truth:EVT-...           ← 可选
```

这 10 个目标足够讲清 A1；不需要把 30+ 条流全部上屏。
