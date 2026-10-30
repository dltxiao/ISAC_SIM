# 07 API 与回放

> **当前状态（2026-10-30）**：实时 REST/SSE 已实现；`/api/trace/frames`、按 step id 查询、增量 `since`、回放、导出、control owner 未实现。
> 本文的 3.2/3.4/4.2/5.3/6 为规划契约；实际可用接口见 §1.1 与 [12 当前实现状态](12-当前实现状态.md)。

## 1. API 原则

1. **读优先**：绝大多数接口只读；写操作只有 pause/resume/step/基线选择。
2. **游标化**：所有增量查询用 `seq`/`cursor`，不靠时间戳猜。
3. **不暴露内部对象**：返回 `WatchTarget/WatchFrame/TraceEvent/StepFrame`，不返回 `Simulation` 引用。
4. **不执行任意表达式**：过滤/断点都是声明式枚举，不做 `eval`。
5. **真值隔离**：`namespace=truth` 默认关闭；需要显式 `demo_truth=true`。
6. **兼容现有**：`/api/stream`、`/api/state` 不动；新接口在 `/api/trace/` 下。
7. **有界**：所有列表有 `limit`，所有 payload 有上限。

## 1.1 当前已实现接口

```text
GET  /trace
GET  /api/trace/capabilities
GET  /api/trace/state
GET  /api/trace/targets
GET  /api/trace/events?after=&limit=
POST /api/trace/step            {"mode":"checkpoint|tick","count":1}
GET  /api/trace/stream          SSE；cursor/状态/StepFrame 变化时发 full snapshot
GET  /api/core  /api/platform  /api/chain  /api/defense
POST /api/session/pause|resume|start|stop|reset
POST /api/attacks               {"type":"A1|A5",...}
```

未实现：`/api/trace/frames`、`/api/trace/step?id=`、`/api/trace/delta`、`/api/trace/replay/*`、`/api/trace/export`、`/api/trace/control/*`、focus/baseline。

## 2. 页面与能力

### 2.1 `GET /trace`

返回 `trace.html`。当前**不解析** `?run`/`?focus`/`?mode`；回放/聚焦未实现。

### 2.2 `GET /api/trace/capabilities`

当前返回：

```json
{
  "schema": "isacsim.trace/1",
  "enabled": true,
  "modes": ["live", "step_tick", "step_checkpoint"],
  "can_control": true,
  "session": {"state": "running", "t": 3.1, "tick": 62, "cursor": 1842},
  "profiles": ["A1", "default"],
  "providers": ["core", "platform"]
}
```

没有 `replay`、`demo_truth`、`limits`、`control_owner`。

## 3. Watch / Trace 查询

### 3.1 `GET /api/trace/targets`

当前直接返回数组：

```json
[
  {"id": "sensor:RSU-01.objects", "kind": "table", "group": "Sensors",
   "label": "RSU-01 · sensing.objects", "fields": [], "key_field": "object_id"},
  {"id": "core:sessions", "kind": "table", "group": "Core",
   "label": "PDU · sessions", "fields": [], "key_field": "session_id"}
]
```

没有 `cursor` 包裹，也没有 `group/kind/focus/include_truth` 过滤参数。

### 3.2 `GET /api/trace/frames`（规划，未实现）

### 3.3 `GET /api/trace/events`

当前返回数组，支持 `after`、`limit`（上限 1000）；**没有** `kind`/`attack_id`/`namespace` 过滤，也不返回 `next_cursor`。

### 3.4 `GET /api/trace/step?id=...`（规划，未实现）

`StepFrame` 完整 before/after 仍在内存；`/api/trace/state` 的 `last_step`/`steps` 只返回摘要。

## 4. SSE

### 4.1 `GET /api/trace/stream`（当前）

无查询参数；不支持 `since`/`targets`/`focus`/`demo_truth`。事件格式只有 `trace` 和心跳：

```text
id: 1842
event: trace
data: {"schema":"isacsim.trace/1","cursor":1842,"frames":{...},"events":[...],"last_step":{...}}

: ping
```

规则：

- 只在 `(cursor, state, last_step.step_id, pause_reason)` 变化时发 full snapshot；
- 无变化时每 5 s 发 `: ping`；
- 没有 `watch`/`status`/`dropped` 事件，也没有 `Last-Event-ID` 恢复；
- 浏览器 `EventSource` 自动重连后重新拿 full snapshot。

### 4.2 与现有 `/api/stream` 的关系

- 现有流继续给主控制台用；
- 追踪页用 `/api/trace/stream`，避免把 10 Hz watch 数据塞进主控制台的 6.7 Hz 快照；
- 两者共享 `MANAGER`，但 payload 独立。

## 5. 控制

### 5.1 `POST /api/trace/mode`（规划）

当前页面直接用 `/api/session/pause|resume`；没有 `/api/trace/mode`。

### 5.2 `POST /api/trace/step`（当前）

```json
{"mode": "checkpoint", "count": 1}
{"mode": "tick", "count": 1}
```

返回：

```json
{
  "snapshot": {"state": "paused", "sim_time": 3.10, ...},
  "trace": {"schema": "isacsim.trace/1", "cursor": 1842, "last_step": {...}}
}
```

约束：

- 无会话 → 409；
- 仿真未暂停时，`Simulation.step()` 会先 `pause("step:prepare")`；
- 当前 `timeout=5.0`，超时返回当前 snapshot，不是 504；
- 没有 `mode="attack"`；
- 没有 `control_owner`/409 并发保护。

### 5.3 `POST /api/trace/baseline`（规划，未实现）

## 6. 回放（规划，未实现）

### 6.1 数据来源

```text
out/web/<run>/
├─ scenario.json
├─ net.json / map.json
├─ truth/events.jsonl
├─ graph.snapshot.jsonl      # 10 Hz 车辆位置
├─ sensing.objects.jsonl
├─ sensing.frame.jsonl
├─ control.command.jsonl
├─ link.metrics.jsonl
├─ v2x.denm.jsonl
├─ v2x.cpm.jsonl
├─ auth.*.jsonl
└─ alerts.jsonl              # 仅 CLI run 可能有
```

### 6.2 回放索引

`ReplayIndex` 在打开 run 时构建：

- 对选中的流建立 `(ts_ns, byte_offset)` 稀疏索引；
- 或者直接加载选中流到内存（当前约 20 个 target，A1/A5 约几 MB）；
- 以 10 Hz/20 Hz 网格合并为 `WatchFrame`；
- 车辆位置从 `graph.snapshot` 重建；
- 告警从 `alerts.jsonl` 或 trace 文件读取；没有则标 `unavailable`。

### 6.3 回放接口

```text
GET /api/trace/replay/runs
GET /api/trace/replay/{run_id}/meta
GET /api/trace/replay/{run_id}/frames?targets=...&from=...&to=...
GET /api/trace/replay/{run_id}/events?after=...
GET /api/trace/replay/{run_id}/steps
```

回放不需要 SUMO；页面地图用 `graph.snapshot` 的节点位置绘制。

### 6.4 回放能力

| 能力 | 支持 | 说明 |
|---|---|---|
| 前进/后退 | 是 | 按帧/按检查点 |
| 拖时间轴 | 是 | seek 到最近帧 |
| 连续播放 | 是 | 0.5×/1×/2×/4× |
| 实时 SUMO | 否 | 只读回放 |
| 攻击注入 | 否 | 只读 |
| 真值 | 是（标注） | 从 truth 文件 |
| 告警 | 视 run | Web run 可能没有 alerts 文件 |

### 6.5 导出

```text
out/<run>/trace/
├─ meta.json
├─ events.jsonl
├─ watch.jsonl
├─ steps.jsonl
└─ summary.md        # 可选：自动生成的讲解卡
```

`GET /api/trace/export?run_id=...` 返回 zip；包含 trace 文件与 `summary.md`。

## 7. 错误码

当前实际：

| 状态 | 场景 |
|---|---|
| 400 | step mode 非法 |
| 409 | 无会话、未运行、追踪未启用 |
| 200 | step 超时返回当前 snapshot（不是 504） |

规划：404/410/429/503/504 见下表。

| 状态 | 场景 |
|---|---|
| 400 | 参数非法（target/step mode/seq） |
| 404 | run/target/step 不存在 |
| 409 | 无会话、未暂停、已有控制者、trace 未启用 |
| 410 | 游标过旧，需重新拉快照 |
| 429 | 客户端请求过频（可选） |
| 503 | TraceService 不可用 |
| 504 | step 超时 |

错误体：

```json
{"detail": "session is not paused", "code": "not_paused", "state": "running"}
```

## 8. 安全

- 默认只读；`step`/`mode` 需要控制权；
- 与主控制台共用现有网络暴露面；建议 `--token`/来源网段限制；
- `demo_truth=true` 只影响 `/api/trace/*`，不影响检测器；
- 所有 label/description 做 HTML 转义（前端默认 `textContent`）；
- 不接受任意正则/表达式；过滤只用枚举/精确匹配；
- 回放路径做目录穿越校验（参考现有 `download` 的 `base not in src.parents`，[session.py](../../isacsim/web/session.py)）。

## 9. 兼容与版本

- `schema` 字段必填；未知字段忽略；
- `GET /api/trace/capabilities` 给前端探测；
- 旧 run 回放：缺 trace 文件时用原始流重建，缺 alerts 标 `unavailable`；
- 新增 target/事件类型不升 major；
- 修改 `WatchFrame.values` 语义或删除字段 → 升 major。
