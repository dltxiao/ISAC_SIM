# 运行追踪与单步调试（Trace / Watch）设计

> 状态：**v1.2 可运行原型**（`feature/trace-watch` @ `ca641b7`，77 项 pytest 通过）。
> 已实现 A1/A5 检查点、core/platform provider 与事件、会话/流表、平台 token/订阅表；**未实现**增量 SSE、回放/导出、origin、focus、单控制者、A2–A4/A6–A10 profile。
> 完整差异与已知限制见 [12 当前实现状态](12-当前实现状态.md)；代码基线为 `master`（含通信层扩展与 SpanContext 命名）。
> 目标读者：仿真后端、Web 前端、攻击剧本作者、演示/验收人员。

## 1. 这是什么东西

在现有仿真控制台之外，增加一个**独立的运行追踪页面**和一套**watch/trace 数据层**：

- 它像一个调试器：可以实时观察若干“被追踪目标”的当前值、变化量、变化原因；
- 它可以在攻击的每个关键步骤自动暂停，展示 before/after；
- 它也可以在没有攻击时使用，作为“仿真运行过程可视化”页面；
- 它不绑定 A1：后续 A2–A10、单体原语都通过“追踪配置”接入，而不是改前端。

一句话：**用调试器的思路追踪攻击链，而不是做一个 IDA Pro 的仿制品。**

## 2. 核心概念

| 概念 | 含义 | 例子 |
|---|---|---|
| `WatchTarget` | 一个被追踪的状态单元；可以是标量、结构体、表格或事件流 | `sensor:RSU-01.objects`、`veh:eastbound.2.state`、`net:pc5` |
| `WatchField` | 目标内部的一个字段，带类型、单位、格式 | `speed`（m/s）、`cbr`（0–1）、`object_count` |
| `WatchFrame` | 某一时刻某个目标的取值 + 相对前一帧/基线的差异 | `t=3.10, objects[+3 ghost]` |
| `TraceEvent` | 一次离散事件：注入、告警、制动、阶段进入 | `ghost:9000 added`、`control.command` |
| `Probe` | 从仿真里采集数据的适配器 | `RecordProbe`、`StateProbe`、`DetectorProbe` |
| `TraceProfile` | 某类攻击的“追踪配置”：检查点、标题、暂停边界 | `A1Profile`、`A5Profile` |
| `WatchProvider` | 与攻击无关的全局域采集器 | `CoreProvider`、`PlatformProvider` |
| `StageSpec` | 当前 profile 的阶段声明：事件 kind + predicate + pause_at | A5 的 S0–S5 |
| `Checkpoint` | 单步暂停的判定点 | “首次出现幽灵”“AMF 过载”“PDU 会话失败” |
| `StepFrame` | 一次单步的结果：区间内的事件 + 前后 watch 快照 | `S1: 目标列表被篡改` |
| `Cursor` | 全局单调游标（`seq` + `tick` + `t`），用于排序、重连、回放 | `seq=1842, tick=62, t=3.10` |

## 3. 总体架构

```text
┌────────────────────────────── 仿真进程 ──────────────────────────────┐
│  Simulation.step_once()                                              │
│    ├─ scheduler.tick() → after_scheduler（A5 S0 停）                 │
│    ├─ sensing/v2x/uu → core.tick() → after_layer("core")（A5 S1/S2） │
│    ├─ auth → platform.tick() → after_layer("platform")（A5 S3）      │
│    ├─ control/task/edge → after_tick（A1 全部、A5 S4/S5）            │
│    └─ ctx.emit(record) → TraceEvent；Snapshot → WatchFrame           │
│                              │                                       │
│                     TraceService                                     │
│    ├─ 全局 target：scene/sensor/fleet/pc5/detector/defense           │
│    ├─ WatchProvider：CoreProvider / PlatformProvider                 │
│    ├─ TraceProfile：A1（S1–S5）/ A5（S0–S5）                          │
│    ├─ TraceStore：frames / events / steps / cursor                   │
│    └─ StepController：pause_at（Condition）/ StepFrame                │
└──────────────────────────────┼───────────────────────────────────────┘
                               │ REST + SSE（当前全量 snapshot）
┌──────────────────────────────┴───────────────────────────────────────┐
│  独立页面 /trace                                                     │
│  左：Watch 树   中：卡片/表格   右：Trace/Xref   底：时间轴/单步     │
└──────────────────────────────────────────────────────────────────────┘
```

## 4. 设计决策摘要（ADR）

| 编号 | 决策 | 理由 |
|---|---|---|
| ADR-01 | 独立页面 `/trace`，不塞进现有控制台 | 现有页面已经承载“控制/统计/编排”，追踪页需要不同的布局和刷新节奏 |
| ADR-02 | 后端新增 Watch/Trace 数据层，前端不解析原始 JSONL | 前端无法可靠地从 30+ 条流里重建“某 RSU 的目标列表”；必须统一投影 |
| ADR-03 | SSE + REST，不引入 WebSocket | 现有栈已经是 SSE；10 Hz 单向推送足够，单步命令走 REST |
| ADR-04 | “寄存器”是 typed watch cell，不是 64 位寄存器 | `objects` 是表、`command` 是事件、`cbr` 是标量；强行统一会失真 |
| ADR-05 | 暂停发生在安全边界（scheduler 后 / tick 末尾），不在 `ctx.emit` 内 | 避免把人的思考时间算进检测时延，也避免阻塞 `stop` |
| ADR-06 | 攻击通过 `TraceProfile` 接入，前端零硬编码 | A2–A10 只需加 profile，不改页面 |
| ADR-07 | 实时只能“冻结/前进”，回退用回放 | 仿真不能倒带；回放从 JSONL + `graph.snapshot` 重建 |
| ADR-08 | 真值单独命名空间，默认折叠 | 检测器不读真值；演示页也不能让观众误以为检测用了真值 |

> 当前落地：ADR-01/02/03/04 已实现；ADR-05 的 `after_scheduler`/`after_layer`/`after_tick` 已实现；ADR-06 已用于 A1/A5，A2–A10 未接入；ADR-07/08 未实现。

## 5. 文档索引

| 文档 | 内容 |
|---|---|
| [01 目标与场景](01-目标与场景.md) | 为什么做、给谁用、成功标准、非目标 |
| [02 总体架构](02-总体架构.md) | 组件、线程、生命周期、实时/单步/回放三种模式 |
| [03 Watch 模型与攻击无关框架](03-Watch模型与攻击无关框架.md) | WatchTarget/Field/Delta、Registry、TraceProfile、A1 映射 |
| [04 Trace 采集与数据契约](04-Trace采集与数据契约.md) | Probe、事件类型、JSON schema、游标、采样、存储 |
| [05 单步断点与时间模型](05-单步断点与时间模型.md) | tick/检查点/断点、暂停确认、StepFrame、层序问题 |
| [06 前端设计](06-前端设计.md) | 页面布局、卡片、表格、时间轴、交互、视觉与性能 |
| [07 API 与回放](07-API与回放.md) | REST/SSE 协议、错误码、回放索引、导出 |
| [08 性能安全与测试](08-性能安全与测试.md) | 预算、背压、鉴权、测试矩阵、验收指标 |
| [09 A1 试点与实施路线](09-A1试点与实施路线.md) | A1 的具体 watch/检查点、分期、风险 |
| [10 v1 实现说明](10-v1实现说明.md) | v1 原型的能力、代码落点、偏差与验证 |
| [11 v2 完善规划](11-v2完善规划.md) | 从 A1 原型到可扩展/可回放追踪的路线图、工作流与验收；含 5G 核心网融合（W7） |
| [12 当前实现状态](12-当前实现状态.md) | **状态锚点**：已实现/未实现、代码落点、已知限制、文档差异 |

## 6. 范围

### 6.1 当前已做

- 一个独立的 `/trace` 页面；
- watch/trace 数据层与 StepFrame；
- A1 的 S1–S5 与 A5 的 S0–S5；
- 无攻击时的传感器/车队/PC5/检测器，以及 5G 核心网/平台节点、UE、会话、流表、token、订阅；
- 实时、暂停、tick/checkpoint 单步。

### 6.2 规划中

- 增量 SSE、回放/导出；
- HookBus/emit origin；
- focus 与单控制者；
- A2–A4、A6–A10 的 profile；
- truth 演示命名空间。

### 6.3 不做

- 不做 Python 源码级单步（pdb / `sys.settrace`）；
- 不做 IDA 式反汇编/十六进制为主的界面；
- 不做逐包协议仿真或 pcap 级追踪；
- 不改变攻击生成逻辑、检测器规则或真值口径；
- 不替换现有控制台；
- v1 不做多人协作、权限分级、云端存储；
- v1 不保证每个原始记录都上屏，允许采样/合并。

## 7. 成功标准（摘要）

1. **能讲清 A1/A5**：A1 S1–S5、A5 S0–S5，2 分钟内讲完；每步能看到“谁改了谁、谁消费了它、产生了什么后果”。（已实现）
2. **无攻击也有用**：展示传感器/车辆/PC5，以及核心网/平台节点、UE、会话、流表、token、订阅。（已实现）
3. **加攻击不改前端**：A1/A5 已通过 `profiles.py` 接入；A2–A10 尚未接入。（部分实现）
4. **单步可控**：检查点在安全边界暂停；暂停后状态不变。（A1/A5 已实现）
5. **低开销**：60 s 场景 trace 开销目标 <5% 墙钟时间；当前 77 项测试通过，尚未做正式 60 s 开销基准。（待验证）
6. **可复现**：同 seed 同参数，单步与连续运行的观测记录/真值一致（排除 `t_wall_ns` 与检测时延）。（部分测试覆盖）
7. **可复盘**：trace.jsonl、回放、导出。（未实现）

## 8. 与现有文档的关系

- 攻击实现细节：[A1 攻击实现解析](../09-A1攻击实现解析.md)；
- 现有架构与数据流：[01 系统架构与数据流](../01-系统架构与数据流.md)；
- 攻击剧本目录：[02 跨层攻击剧本目录](../02-跨层攻击剧本目录.md)；
- Web 控制台：[05 Web 控制台使用说明](../05-Web控制台使用说明.md)。

## 9. 阅读路径

- **产品/演示人员**：01 → 06 → 09；
- **后端**：02 → 03 → 04 → 05 → 07；
- **前端**：02 → 06 → 07；
- **测试/验收**：01 → 08 → 09。
