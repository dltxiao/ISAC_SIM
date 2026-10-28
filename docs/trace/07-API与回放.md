# 07 API 与回放

## 1. API 原则

1. **读优先**：绝大多数接口只读；写操作只有 pause/resume/step/基线选择。
2. **游标化**：所有增量查询用 `seq`/`cursor`，不靠时间戳猜。
3. **不暴露内部对象**：返回 `WatchTarget/WatchFrame/TraceEvent/StepFrame`，不返回 `Simulation` 引用。
4. **不执行任意表达式**：过滤/断点都是声明式枚举，不做 `eval`。
5. **真值隔离**：`namespace=truth` 默认关闭；需要显式 `demo_truth=true`。
6. **兼容现有**：`/api/stream`、`/api/state` 不动；新接口在 `/api/trace/` 下。
7. **有界**：所有列表有 `limit`，所有 payload 有上限。

## 2. 页面与能力

### 2.1 `GET /trace`

返回 `trace.html`。可选查询：

- `?run=<run_id>`：打开历史 run 的回放；
- `?focus=<attack_id>`：默认聚焦某攻击；
- `?mode=live|replay`。

### 2.2 `GET /api/trace/capabilities`

返回 schema、支持的模式、当前会话、是否可控制。

```json
{
  "schema": "isacsim.trace/1",
  "session": {"run_id": "...", "state": "running", "t": 3.1, "tick": 62},
  "modes": ["live", "step_tick", "step_checkpoint", "replay"],
  "can_control": true,
  "demo_truth": false,
  "limits": {"max_targets": 30, "max_rows": 200, "max_events": 500},
  "profiles": ["A1", "default"]
}
```

## 3. Watch / Trace 查询

### 3.1 `GET /api/trace/targets`

```json
{
  "targets": [
    {"id": "sensor:RSU-01.objects", "kind": "table", "group": "Sensors",
     "label": "RSU-01 · sensing.objects", "rate_hz": 10},
    {"id": "net:pc5", "kind": "struct", "group": "Network",
     "label": "PC5 · link metrics", "rate_hz": 10}
  ],
  "cursor": 1842
}
```

参数：`group`、`kind`、`focus`、`include_truth`。

### 3.2 `GET /api/trace/frames`

```json
GET /api/trace/frames?targets=sensor:RSU-01.objects,net:pc5&since=1800&limit=50
```

返回 `WatchFrame[]` + `next_cursor`。用于：

- 页面初始加载；
- 断线重连补齐；
- 回放 seek。

### 3.3 `GET /api/trace/events`

```json
GET /api/trace/events?after=1800&limit=200&kind=alert,attack.start&attack_id=ATK001
```

返回 `TraceEvent[]` + `next_cursor`。`kind` 可逗号分隔；`namespace` 默认 `observation`。

### 3.4 `GET /api/trace/step?id=...`

返回某个 `StepFrame`；用于回看/分享链接。

## 4. SSE

### 4.1 `GET /api/trace/stream`

参数：

- `since` / `Last-Event-ID`：从哪个 seq 开始；
- `targets`：只订阅指定 target（逗号分隔）；
- `focus`：只推该攻击的事件（全局状态仍推）；
- `demo_truth`：是否包含 truth（默认 false）。

事件格式：

```text
id: 1842
event: watch
data: {"schema":"isacsim.watch/1","seq":1842,"target_id":"sensor:RSU-01.objects",...}

id: 1843
event: trace
data: {"schema":"isacsim.trace/1","seq":1843,"kind":"alert",...}

event: status
data: {"state":"paused","t":3.10,"tick":62,"mode":"step"}

event: dropped
data: {"watch":12,"events":0,"since":1800}

event: heartbeat
data: {"t":3.10,"seq":1843}
```

规则：

- `watch` 可以合并/丢中间帧；`trace` 关键事件不丢；
- `status` 在状态变化时发；
- `dropped` 在客户端落后时发，UI 显示提示；
- 心跳 5 s，防代理超时；
- `retry: 1500` 让浏览器自动重连；
- 支持 `Last-Event-ID` 恢复。

### 4.2 与现有 `/api/stream` 的关系

- 现有流继续给主控制台用；
- 追踪页用 `/api/trace/stream`，避免把 10 Hz watch 数据塞进主控制台的 6.7 Hz 快照；
- 两者共享 `MANAGER`，但 payload 独立。

## 5. 控制

### 5.1 `POST /api/trace/mode`

```json
{"mode": "live"}
{"mode": "paused"}
```

返回 `status`。与现有 `/api/session/pause|resume` 兼容；追踪页优先用自己的 mode，以便携带 reason。

### 5.2 `POST /api/trace/step`

```json
{"mode": "checkpoint", "count": 1}
{"mode": "tick", "count": 1}
{"mode": "attack", "attack_id": "ATK001"}
```

返回：

```json
{
  "step": {"schema": "isacsim.step/1", "step_id": "...", ...},
  "status": {"state": "paused", "t": 3.10, "tick": 62},
  "cursor": 1842
}
```

约束：

- 无会话 → 409；
- 仿真未暂停 → 先 pause 再 step，或返回 409（由 `auto_pause=true` 控制）；
- 超时 → 504，`status` 仍可查；
- 同一会话只允许一个控制客户端（`control_owner`）。

### 5.3 `POST /api/trace/baseline`

```json
{"target_id": "sensor:RSU-01.objects", "seq": 1800}
```

设置 diff 基线；仅影响该客户端视图，不改历史。

## 6. 回放

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
- 或者直接加载选中流到内存（A1 的 10 个 target 约几 MB）；
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
- 回放路径做目录穿越校验（参考现有 `download` 的 `base not in src.parents`，[session.py](../../isacsim/web/session.py:143)）。

## 9. 兼容与版本

- `schema` 字段必填；未知字段忽略；
- `GET /api/trace/capabilities` 给前端探测；
- 旧 run 回放：缺 trace 文件时用原始流重建，缺 alerts 标 `unavailable`；
- 新增 target/事件类型不升 major；
- 修改 `WatchFrame.values` 语义或删除字段 → 升 major。
