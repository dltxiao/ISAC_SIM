# 更新记录

## v0.3.0-dev（未发布）

### 新增

- **语义级 5G-A 核心网**（M0/M1）：AMF/SMF/UPF/UDM/AUSF 状态机与 CoreBus；注册、鉴权、PDU 会话、QoS flow、TEID/流表；输出 `core.nas`/`core.session`/`core.qos`/`core.handover`/`core.upf`。
- **Record/Truth v2**：`isacsim.record/2`，增加可选 `trace_id`/`span_id`/`parent_span_id`/`chain_id`；新增 `schemas/record.v2.json`、`schemas/truth.v2.json`。
- **Uu 几何驱动**：附着/切换基于 gNB 覆盖与 FOV，不再按 12–35 s 定时器翻转 `CELL-1/2`；`uu.session` 带 `gnb_id`/`pdu_session_id`/`5qi`/`latency_ms`。
- **图 v2**：节点带 `layer`，新增 `veh→gNB` 无线边与 `gNB→AMF→SMF→UPF→DN` 核心网边。
- **真值一致性测试**：A1–A4 的 `domains` 必须能在本次运行的观测流中找到对应证据。
- **Core 测试**：注册/会话/QoS/UPF、trace 传播、core 关闭、Uu attach、A4 truth 修复。
- **语义级 V2X 平台**（M2）：NEF token/订阅 + PlatformLayer（IAM/Ingest/订阅/云控/审计）；输出 `core.nef`、`app.auth`、`app.ingest`、`app.subscription`、`app.control`、`app.audit`。
- **平台测试**：Ingest、控制意图到 ControlLayer、坏 token 拒绝、NEF token/订阅。
- **扩展剧本**（M3）：A5 信令风暴（AMF 过载/会话失败/平台延迟/远控）、A7 UPF/N6 路由劫持 + 伪造 CPM、A9 租户 token 复用 + 批量云控。
- **防御动作**：`defense.action`；`amf_rate_limit`、`smf_release_session`、`upf_route_restore`、`platform_revoke_token`、`platform_quarantine_source`。
- **新检测规则**：`nas_registration_flood`、`upf_route_drift`、`ingest_provenance_mismatch`、`cloud_control_burst`。
- **A6/A8/A10**（M4）：QoS 降级、NEF/订阅篡改、雷达+信令+审计抑制组合。
- **链构建器**：`detect/chain.py` 输出 `chains.json`；`eval/chain_metrics.py` 计算节点召回/精确率、根因 top-1、阶段覆盖。
- **链级实测**（20+20，A1–A10）：节点召回 55.28%、精确率 100%、根因 top-1 70%、阶段覆盖 100%。
- **Web 视图**（M5）：网络/平台 tab、核心网/平台摘要、防御动作表、攻击链列表；新增 `/api/core`、`/api/platform`、`/api/chain`、`/api/defense`。
- **NIDD 导出**（M5）：D4（核心网）、D5（平台）、`defense.jsonl`、`chain.json`。
- **v0.3.0 验收**（M6）：500 攻击 + 500 正常（A1–A10 各 50，15 s，18 进程）识别率 **100%**、误检率 **0.20%**、P99 **0.214 ms**、链级节点召回 **55.28%**；批量 **198.4 s**；60 s 场景 **16.8 s**；报告见 [docs/11](docs/11-v0.3.0验收报告.md)。
- **核心网状态可视化**：左侧 Canvas `路网/叠加/核心网` 切换，节点按 green/orange/red 着色；`layers/health.py` 统一阈值；A5/A6/A7/A8/A9/A10 状态已实测。
- **开发产物清理**：`isacsim clean` 默认预览、只清 Web 会话并保留最近 N 个；`--include-batches` 显式清理批量/实验目录；自动保护正在写入的会话与 `--protect` 路径。
- **运行追踪 v1**：独立 `/trace` 页面、watch/trace 数据层、A1 S1–S5 检查点单步、SSE；见 [docs/trace/10](docs/trace/10-v1实现说明.md)。
- **运行追踪 v2 规划**：`TraceProfile`/`WatchProvider`、S0、增量 SSE、trace.jsonl/回放、origin 与 5G 核心网融合（W7）；见 [docs/trace/11](docs/trace/11-v2完善规划.md)。
- **`/trace` 核心网/平台 provider**：无攻击时也显示 `core:summary`、8 个核心网节点、`core:ues`、`platform:summary`；节点 health 直接进入 watch 卡片。

### 文档

- [`docs/01-系统架构与数据流.md`](docs/01-系统架构与数据流.md) 扩充为字段级数据字典：
  时间/坐标基准、统一信封、实体×产出总览、逐流字段说明（含单位、生成方式、用途）、
  攻击改写对照、真值/告警/附属文件、消费者矩阵、数据模型已知边界。

### 修复

- A4 真值移除未实现的 `uu:ghost_attach` / `uu` 域；A4 只声明实际可观测的 `auth`、`v2x`。
- A1 改为选择“由目标 RSU 服务且装备 ADAS”的车辆，保证 `control` 域可观测。
- 记录 schema 升为 v2；版本号 → `0.3.0`。
- `sensing.frame.object_count` 改为与同帧 `sensing.objects` 实际条数一致（此前不含攻击注入项）。
- 基站传感器产生的记录实体前缀由 `rsu:` 改为 `gnb:`（`sensing.objects`/`sensing.frame`/`ghost_object` 告警）。
- 回波 `snr_db` 与检测概率口径统一：卡车增加 3 dB 处理增益（此前判决用了、记录没用）。
- **命名解耦**：分布式 `Trace` 类型改为 `SpanContext`，`ctx.trace` 改为 `ctx.span`；`record.trace_id/span_id` 字段保持不变，为 `/trace` 观测追踪预留命名边界。

### 测试

- 74 项测试通过（合并后：核心网/平台/链/追踪/Provider 等）。

---

## v0.2.0（2026-10-27）

### 新增

- **实现说明** [`docs/07-实现原理与数据生成.md`](docs/07-实现原理与数据生成.md)：项目结构、18 类数据生成器、
  攻击注入机制、7 条检测规则与 truth 隔离说明。
- **7 张示意图**（`figs/`）：场景布局、正常态数据流与闭环、A1–A4 因果链、攻击 × 观测域矩阵与跨源比较。
- `figs/gen_figs.py`：SVG 生成 + Chromium 2× PNG 渲染；`figs/check_layout.py`：文字重叠/越界粗检。

### 修复与改进

- `echo_suppress`：删除回波时点迹也不再产生（目标列表仍保留），与"雷达丢批"语义一致。
- 攻击调度：`start=None` 时多实例按 2 s 错峰启动，与显式指定起始时刻的行为一致。
- `streams.graph` 开关生效（此前图快照总会输出）。
- Web 统计增加"真值事件"计数；版本号由 `/api/hardware` 下发，前端不再硬编码。
- 文档 04 的优化项按实测修正（回波/点迹才是记录量主因）。

### 测试

- 新增"检测器不得导入或访问 truth"的静态断言。
- 共 30 项测试通过。

### 版本

- `isacsim.__version__`、`pyproject.toml` → `0.2.0`；真值中的 `generator_version` → `isacsim-0.2.0`。

---

## v0.1.0（2026-10-27）

首个可运行版本。

- **路线 B**：SUMO 1.28（pip wheel，无需 root）+ TraCI，单机单会话；路网、车流、信号灯由 SUMO 提供。
- **18 类观测流**：感知（回波/点迹/目标列表/帧/GNSS）、V2X（CAM/CPM/DENM/SPaT）、Uu、认证、控制、任务、链路、图。
- **攻击**：A1 感知篡改→拥塞、A2 物理欺骗+控制误导、A3 跨层隐蔽、A4 双因素仿冒；8 类单体原语；运行时调度与事件级真值。
- **检测与评估**：参考检测器（只读观测流）、告警聚类、事件匹配、Wilson 置信区间、多进程数据集生成。
- **Web 控制台**：FastAPI + SSE + Canvas；实体可视化、数据/攻击统计、开始/暂停/停止/复位、攻击编排、产物下载。
- **数据出口**：原生 JSONL/Parquet + 5G-A-NIDD 适配（D0–D3、CAM pcapng、labels）。
- **文档与测试**：docs/00–06、29 项测试、一键脚本。

验收（500 攻击 + 500 正常，15 s/场景）：

| 指标 | 结果 |
|---|---|
| 识别率 | 100%（95% CI 99.24%–100%） |
| 误检率 | 0.2%（1/500） |
| 检测时延 | P50 0.08 ms / P95 0.20 ms / P99 0.26 ms |
