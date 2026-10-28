# 03 Watch 模型与攻击无关框架

## 1. 为什么需要一层 Watch 模型

前端如果直接订阅 `sensing.objects` / `v2x.cam` / `link.metrics`，会遇到四个问题：

1. **流太多**：60 s 场景 18 类流、21 万条记录；全量推给浏览器不现实；
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

### 3.1 全局目标（无攻击也注册）

| ID | 类型 | 来源 | 说明 |
|---|---|---|---|
| `scene:summary` | struct | `snapshot` | 车辆数、RSU 数、仿真时间 |
| `sensor:<id>.objects` | table | `sensing.objects` / `last_objects` | 每个传感器一个 |
| `sensor:<id>.frame` | struct | `sensing.frame` | 帧号、对象数、时钟偏移 |
| `veh:<id>.state` | struct | `snapshot.vehicles` | 位置、速度、朝向、flags |
| `veh:fleet` | table | `snapshot.vehicles` | 车队总表；默认只显示受影响/选中 |
| `net:pc5` | struct | `V2XLayer` | offered/CBR/PER/delay/DCC/jam |
| `det:alerts` | event/table | `detector.snapshot.recent` | 最近告警 |
| `graph:scene` | table | `graph.snapshot` | 可选，车辆/覆盖关系 |

这些目标与攻击无关，保证“正常运行可视化”。

### 3.2 攻击目标（由 profile 注册）

| ID | 类型 | 来源 | 说明 |
|---|---|---|---|
| `atk:<attack_id>` | struct | `AttackInstance` | 类型、t0/t1、参数、目标 |
| `atk:<attack_id>.stages` | table | profile | 阶段/检查点 |
| `atk:<attack_id>.effects` | table | trace | 该攻击造成的变化摘要 |
| `truth:<event_id>` | struct | `TruthStore` | 可选，演示命名空间 |

### 3.3 命名规范

```text
<domain>:<entity>.<aspect>
```

- `domain`：`scene` / `sensor` / `veh` / `net` / `det` / `atk` / `truth`；
- `entity`：场景内稳定 ID（RSU-01、eastbound.2、ATK001）；
- `aspect`：`objects` / `state` / `frame` / `stages` / `alerts`；
- 避免在 ID 里放易变的时间/序号。

## 4. `TraceProfile`：攻击无关的接入点

### 4.1 接口（设计，不是最终代码）

```python
class TraceProfile:
    attack_type: str

    def targets(self, ctx, inst) -> list[WatchTarget]:
        """该攻击要额外注册的目标（可引用全局目标）。"""

    def checkpoints(self, ctx, inst) -> list[Checkpoint]:
        """该攻击的单步检查点。"""

    def annotate(self, event, ctx, inst) -> TraceEvent:
        """给事件补充 attack_id/stage/narrative/caused_by。"""

    def narrative(self, stage_id, values) -> str:
        """该阶段给观众看的一句话。"""
```

约束：

- profile **不执行攻击**，只描述“看什么/何时停/怎么讲”；
- profile 可以读 `inst` 的公开字段，不直接改仿真；
- 默认实现 `DefaultProfile` 覆盖单体原语；
- `PROFILES = {"A1": A1Profile, ...}`，未注册的攻击用默认；
- profile 的变更不应要求前端发版。

### 4.2 检查点 DSL（声明式）

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

### 5.1 A1

- 额外目标：`atk:A1`、目标 RSU 的 `objects`（已存在全局目标，profile 只加高亮/说明）、`truth:A1`（可选）；
- 检查点：选目标 → 首次注入 → 首次 TTC → CBR 跃升 → 告警；
- 讲解重点：目标 RSU vs 其他 RSU 的对照。

### 5.2 A2

- 额外目标：目标 RSU 的 `objects`（stop_sign）、`veh:<victim>.state`（brake）、`net:pc5`（jam/PER）；
- 检查点：伪造停止牌 → 注入制动 → 链路干扰 → 急刹；
- 复用同一套表格/标量渲染。

### 5.3 A3

- 额外目标：`sensing.radar.echo`（目标车）、`sensing.radar.track`、`control.command`；
- 检查点：回波偏置开始 → 点迹偏移 → 60% 小指令 → 目标列表仍正常；
- 需要“series”渲染看偏置随时间变化。

### 5.4 A4

- 额外目标：`auth.cert`、`auth.fingerprint`、`v2x.cam`（克隆）、`veh:<victim>.state`；
- 检查点：证书克隆 → 指纹复制 → 伪 CAM → 位置跳变告警。

### 5.5 A5–A10（future）

- 按域增加 `core.*` / `app.*` / `platform.*` 目标；
- 前端仍用 `struct/table/event` 渲染器；
- 只要 profile 遵守同样的 schema，不需要改页面。

### 5.6 新增一个攻击的接入步骤

以 A2 为例：

1. 新建 `profiles/a2.py`，继承 `TraceProfile`；
2. `targets()` 返回它需要的额外 target（如 `sensor:<rsu>.objects`、`veh:<victim>.state`、`net:pc5`），全局 target 不重复注册；
3. `checkpoints()` 用声明式 DSL 写 4–5 个检查点（伪造停止牌、注入制动、链路干扰、急刹）；
4. `narrative()` 写每个检查点的一句话；动态值用 `{victim}`/`{jam}` 占位；
5. 在 `PROFILES` 注册；跑 A2 的集成测试；
6. 前端不做任何改动，最多在 profile 里加一个 `render` 提示。

> 如果某个攻击没有显式阶段事件，先用时间/首记录/首变化检查点；不要为了 trace 去改攻击逻辑。等剧本自然演化出 stage 事件后再替换。

## 6. 默认 profile 与“无攻击”

- 启动会话即注册全局目标；
- 没有攻击时，`atk:*` 为空，页面显示“当前无攻击”，但仍展示传感器/车辆/PC5/DET；
- 单体原语使用 `DefaultProfile`：目标 + “首次变化/首次告警/结束”三个检查点；
- 这样“运行过程可视化”不是攻击功能的附属，而是基础能力。

## 7. 值语义与边界

### 7.1 观察值 vs 真值

- `sensor:*`、`veh:*`、`net:*`、`det:*` 来自观测/仿真状态；
- `truth:*` 来自真值，默认折叠，标注“仅演示/评估”；
- 检测器不读 trace/truth；测试要断言这一点。

### 7.2 派生字段

- `ghost` = `entity.startswith("ghost:")`；
- `affected` = 被 ADAS 制动/速度下降/被标记；
- `delta` = 由 store 计算，不由前端算；
- 派生字段必须在 metadata 里标 `derived`，避免误解为原始字段。

### 7.3 缺失值

- `null` 表示“该帧没有这个字段”，不是 0；
- `stale` 表示“目标存在但本帧未更新”；
- `error` 表示“采集失败”，要显示原因；
- 前端不得用 0 填补缺失值。

## 8. 版本与兼容

- `WatchTarget.schema_version` / `TraceEvent.version`；
- 新增字段向后兼容；删除/改义升 major；
- 前端遇到未知 `kind` 降级为 JSON 折叠；
- 旧 run 回放缺少 trace 文件时，用 `ReplayAdapter` 从原始流重建；
- profile 版本写入 trace 头部，便于复现。

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

这 10 个目标足够讲清 A1；不需要把 18 类流全部上屏。
