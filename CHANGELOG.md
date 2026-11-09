# 更新记录

## 未发布（/trace 攻击阶段指示栏，2026-11-09）

### 新增

- **攻击阶段指示栏**：`/trace` 中栏顶部显示当前攻击的 S0–END 阶段状态、相对时间与
  最近 3 条证据（可展开全部）；点击阶段或证据跳到对应监控面板并高亮卡片。
  A2 的 `RSU-01 +1 ghost target(s)` → 感知 / RSU-01 已验证。
- 后端：`TraceService` 新增 `_collect_live_evidence()`（增量扫描、非单步模式也实时推进）
  与 `snapshot()["stage_bar"]`；`_attacks` 现在保留 type/t0/t1 等完整信息（顺带修复
  时间轴攻击标记取不到 t0 的问题）。
- 前端：`#stageBar`、`renderStageBar()`、`resolveTargetId()`（`veh:x`→`veh:x.state`）、
  `applyFlash()`；多攻击时提示“另有 N 起”。

### 测试

- 新增 `tests/test_stage_bar.py`（5 项：无攻击/等待/A2 幽灵/结束/单体原语）；全量 164 项通过。

## 未发布（/trace 界面微调，2026-11-09）

### 界面

- **注入栏聚合**：`/trace` 顶部的攻击类型、强度、时长、注入按钮收进 `.inject-group`，
  按钮紧跟时长（原来 `margin-left:auto` 把它甩到最右，和选择框“不像一组”）；
  攻击说明文字单独放右端，窄屏自动换行。
- **卡片网格对齐**：中间面板 `.cards` 改为 `align-items: stretch` + `grid-auto-flow: row dense`，
  同一行卡片等高、空位自动回填；卡片内部 `.card-body` 纵向撑满，`.node-grid` 行高一致
  （`grid-auto-rows: 1fr`），核心网节点 chip 不再参差。

## 未发布（健康度改为瞬时状态，2026-11-09）

### 修复

- **核心网健康度不再用累计计数**：`CoreLayer` 按 2 s 窗口重算 AMF/SMF/UDM/AUSF 的拒绝占比；
  A5 风暴结束后 AMF/SMF 会回到绿色，而不是永久红色。
- **UPF 路由恢复**：`restore_route()` 清空 `route_override` 并恢复 flow 路由；旧实现把
  `route_override` 置成 `"dn:DN-01"`，健康度仍按“被覆盖”判红，A7 之后 UPF 永久异常。
- **UPF 路由健康度改用白名单**：`route_override` 在 `core.allowed_dns` 内（如合法割接 DN-02）
  不算异常；只有 EVIL 之类不在名单里的才判红。
- **NEF 健康度改为“最近 2 s 事件”**：退订/吊销 token 只在发生后 2 s 内计入，不再用累计
  `unsubscribed/revoked`，A8 之后 NEF 会恢复绿色。
- **平台健康度改为“最近 2 s 事件”**：ingest 丢弃/来源异常/慢处理、IAM 拒绝、云控拒绝/突发、
  审计抑制、订阅吊销都按窗口统计；A7/A8/A9/A10 之后平台节点会恢复绿色。
- **平台 ingest 的 provenance 判定改用 `allowed_dns` 白名单**：合法 DN-02 割接不再计入
  `provenance_bad`。

### 测试

- 新增 `tests/test_core_health.py`：A5/A7/A8/A9/A10 与合法割接的“攻击中变红、攻击后恢复”回归。

## 未发布（评估口径与正常集，P0+P1，2026-11-09）

### 新增（R5 难负样本）

- `isacsim/batch/benign.py`：批量正常场景按时间表执行 9 类**合法运维**——QoS 调整
  （79→8、800 kbps）、N6 主备割接（DN-01→DN-02）、租户单类型退订/恢复、token 轮换、
  单批车队控制（带 TTC）、真实急刹 + DENM。`meta.benign_ops` 可审计。
- 批量正常场景改走 `step_once + BenignOps.tick`；交互式运行默认不注入。

### 修改（规则去标记化 / 阈值化）

- `qos_downgrade`：5QI≤9 **且** GFBR<500 kbps（合法 8/800 不报）。
- `upf_route_drift` / `ingest_provenance_mismatch`：改查 `core.allowed_dns` 白名单
  （DN-02 合法割接不报，A7 的 EVIL 仍报）。
- `subscription_tamper`：一次 revoke 掉 **≥2 类**事件才报（单类型退订不报）。
- `cloud_control_burst`：2 s 内 **≥3 批**、每批 ≥3 车（单批/两批合法车队控制不报）。
- `cpm_degraded`：窗口 24 帧、丢包 **≥14**（≈58%）；合法 QoS 调整 p≈0.15 不再误报。
- 云控 TTC 贯通：`dispatcher.submit(params.ttc_s)` → `control._apply_intents`；
  无危险依据时才落到 99。
- `qos_impairment`：5QI 5–8 的基础丢包率下调（合法切片调整只掉 ~15%），5QI 9 保持原值。
- 批量 seed：正常场景与攻击使用同一段 seed（配对），不再用 100000 起的另一段。

### 修改（R7 链级口径）

- `ChainBuilder` 输出 `"model": "alert_skeleton"`；`chain_metrics` 输出
  `stage_ratio_kind=alert_count_skeleton` / `root_cause_kind=first_alert_entity`；
  报告改称“阶段骨架覆盖”“根因 top-1（首告警实体）”，并加口径说明。

### 测试

- 新增 `tests/test_core_nf.py`（NF 排队/卸载/显示负载）、`tests/test_platform_subscription.py`
  （shrink/add_events）、`tests/test_benign_ops.py`（9 类合法动作 + 0 告警）、
  `tests/test_detector_rules.py`（5 条规则阈值）。
- 新增 `scripts/measure_a3_sweep.py`（含同 seed 无攻击对照）。

### 重测结果（500 攻击 + 500 正常，15 s，a=0.8）

| 指标 | 结果 |
|---|---|
| 识别率 | 100.00%（500/500，95% CI 99.24%–100%） |
| 误检率 | 0.00%（0/500，95% CI 0%–0.76%） |
| 时延 | P50 0.03 / P95 0.16 / P99 0.345 ms（n=7696） |
| 链级 | 节点召回 58.77%、精确率 96.70%、根因 top-1 60.00%、阶段骨架 98.00% |
| 运行成本 | 1000 场景 / 12 进程 / 269 s / 41 MB（不落流） |

A3 强度曲线（每档 20 seed + 同 seed 对照）：雷达规则 0.45 以上 18/20、低幅度 0/20；
诱导 ADAS 误刹 14/20；伪指令 20/20。旧表 3/3 不再引用。

### 文档

- `docs/03 §6.1/6.2/6.4/6.5`、`docs/07 §4.7/4.8`、`docs/02 §4`、`docs/09 §4` 按新口径重写；
  `docs/01` 的 “A1/A5 StageSpec” 更正为 A1–A10；`docs/attacks/A6` 的 `cpm_degraded` 阈值同步。

## 未发布（审核整改 A9–A10，2026-11-09）

### 修复（对应 docs/attacks/review-2026-11-06 的 A9/A10 条目）

- **R1 A9 立即启动**：新增 `ready()` 等平台租户 token（首个 `platform.tick` 后）；旧实现
  `start=None` 时零效果且**不写真值**。实测现在 `batches>=3`、有 `control.command`。
- **R12 A9 批次节奏**：`Pacer(2 Hz)`；首拍不再双发，周期不再从 0.5 s 漂到 0.55 s。
  3 s 实测 6 批（旧实现 7 批：双发 + 5 个 0.55 s 周期）。
- **R9 A9/A10 幅度**：A9 增加 `decel=2.5+1.5a`（victims 3–8 保留）；A10 映射到
  `bias=1+5.5a`、`signal=1+4a 条/0.2s`、`audit=2+2a`、`brake=0.8+1.2a`。
- **R13 target**：A9 的 target 强制进入 victims 首位；A10 的 target 直接作为 victim。
- **R4 A9 去标记**：删掉 `params.replayed`；`cloud_control_burst` 改为“2 s 内 ≥2 批、
  每批 ≥3 车”的频率判定，不再读标记。
- **R14 A10 注入点**：victim 从“当前被某传感器看到”的车里选；服务传感器在 tick 里
  按目标列表锁定，`stop()` 回填 `params.sensor/sensor_locked` 与 `nodes`。
- **R11 A10 速率**：`Pacer(5 Hz)`，信令 5–25/s 与真值一致（旧实现实际 20/s、真值写 25）。
- **R17 真值**：A9 记 `batches/commands/actual_rate/brake_decel`；A10 记
  `sensor/sensor_locked/signaling_sent/audit_suppressed/brake_decel`；包络改实测判据。

### 修复（审核未提）

- **A10 雷达腿“结构上不可检”**：旧 hook 不带 `sensor_id` 过滤，所有看到 victim 的站
  同幅度偏置 → 跨站位置差恒为 0，`radar_range_bias` 永远不触发（实测 3 s/6 s 都是 0）。
  现在只偏服务传感器：a=1.0（6.5 m）实测能命中，a=0.8（5.4 m）仍不触发。
- **A9 周期漂移**：除首拍双发外，`ctx.t - _last >= 0.5` 在浮点边界判负，实际 0.55 s/批。
- **A10 包络恒真**：`bias>=1.0` 改为 `bias>=3.0 且 signaling>=15/s`；A9 `vehicles>=3`
  改为 `vehicles>=5 且 decel>=3.5`。

### 变更（文档/接口）

- A9/A10 文档 §2/§3/§4/§5/§6/§7/§8/§9/§10/§12/§13 按代码重写；
  A10 §4.2 的 `bot=True` 旧代码、§5 的洪泛告警、§10.7 的“1.5 m 残差”全部删除。
- `docs/02`、`docs/03 §5`、`docs/attacks/README` 同步 A9/A10 的参数与包络。

### 测试

- 新增 `tests/test_a910.py`（11 项：A9 ready/节奏/幅度/target/去标记/频率告警；
  A10 传感器锁定/单站偏置/雷达可检性/幅度/取消清理）；全量 `pytest` 通过（139 项）。

### 注意

- A10 默认幅度（a=0.8，5.4 m）**低于** `radar_range_bias` 的 6 m 门槛，雷达规则不会触发；
  默认幅度下能抓到它的是 `audit_gap` 与 `brake_without_hazard`，不要写成“四域都会报警”。
- A9 的 token 仍是“假定已泄露”（直接读 `platform.tenant_token_id`），未建模获取过程。

## 未发布（审核整改 A7–A8，2026-11-09）

### 修复（对应 docs/attacks/review-2026-11-06 的 A7/A8 条目）

- **R16-A7 真因果**：平台 ingest 的 `provenance` 不再写死 `dn:DN-01`，改为由
  `upf.route_override` 派生。路由改动后**真实** CPM/SPaT/DENM 的 `app.ingest` 全部带
  `dn:EVIL-01`（实测约 30 条/s），恢复路由后自动回 `dn:DN-01`；伪造 CPM 只承担内容篡改。
- **R1 A8 立即启动**：新增 `ready()` 等平台订阅 bootstrap（约 t=0.05）。旧实现
  `start=None` 时撤不到任何订阅，0 条 `no_subscription`，却“看起来跑完了”。
- **R9 A7/A8 幅度生效**：A7 → 注入频率 1–5 Hz、目标数 1–4、制动 2–5 m/s²；
  A8 → 退订范围 denm → denm+spat → denm+spat+cpm、制动 1.5–3.5 m/s²。
- **R12 A7 首拍双发**：改用 `Pacer`，3 s、4.2 Hz 实测 13 条（旧实现固定 0.5 s + 首拍双发 = 7 条）。
- **R13 target**：A7/A8 支持 `spec.target`，真值记 `target_matched`。
- **R17 真值**：A7 记 `inject_rate/injected/actual_rate/route_restored`；A8 记
  `revoked_event_types/ingest_dropped/nef_leg_effect`；包络改用实测强度。
- **R18 文档**：A7/A8 的 §3/§4/§7/§8/§9/§10/§12/§13 全部按代码重写；
  删掉“cancel 不执行 stop”（R2 已修）与“amplitude 无效”等过期结论。

### 修复（审核未提、但会导致“假成功”的两个洞）

- **A8 第二次运行无效**：旧实现固定撤 `subs[0]`，第一次攻击的 `stop()` 重建订阅后，
  第二次会撤到一条已 REVOKED 的订阅——`revoked` 计数 +1、事件也发，但 0 条 dropped。
  现在按 `state=ACTIVE 且覆盖 denm` 选目标，两次串行实测两个窗口都有 drops。
- **A8 人造 80 ms 平台延迟**：它不是 A8 的语义，却让 A8 靠 `ingest_latency_spike`
  被“借来的规则”检出。已删除；改为真实行为证据。

### 变更（接口/口径）

- `SubscriptionManager.shrink(ctx, sub_id, event_types)`：按事件类型部分退订，
  记录里带 `event_types`（被摘掉的）与 `remaining`（剩下的）。
- 新增检测规则 `ingest_drop_spike`：1 s 内 `app.ingest` 且 `cause=no_subscription` ≥10 条。
- A7/A8 包络改为实测判据（`inject_rate >= 3Hz`、`revoked_event_types >= 2`），
  旧的 `"route redirect"` / `unsubscribe >= 1` 恒真。
- trace：A7 S2 改名“流量改向/伪造 CPM 入平台”，A8 S1/S2/S3 分别改为
  “NEF API 滥用（无下游）/ 平台订阅被缩减 / 平台成片漏事件”。

### 测试

- 新增 `tests/test_a78.py`（9 项：A7 首拍/真实 provenance/幅度/取消恢复路由；
  A8 立即启动/退订范围/二次运行/无人工延迟/NEF 腿声明）；全量 `pytest` 通过（128 项）。

### 注意

- A7 的 provenance 改向会让 `app.ingest` 在攻击窗口内的分布与旧样本不同，
  `docs/03 §6.1` 的 500+500 仍未重测；
- A8 的 NEF 腿仍无下游（`params.nef_leg_effect="none"`），且 NEF `issue_token`
  在本环境对任何主体开放——这是已知简化，见 A8 §10。

## 未发布（审核整改 A4–A6，2026-11-06）

### 修复（对应 docs/attacks/review-2026-11-06 的 A4–A6 条目）

- **R1 立即启动不再静默失效**：新增 `AttackInstance.ready(ctx)` 前置条件 + `abort()`；
  未就绪时保持排队（最多 `READY_TIMEOUT_S=1 s`），超时才中止并且**写一条带
  `observed.aborted/blocked_reason` 的真相**。A6 在 `start=None` 时会等到首个 PDU 会话（约 0.15 s）再跑。
- **R4 检测去标记化**：`nas_registration_flood` 改为“1 s 注册速率 ≥300 且拒绝占比 ≥10%”，
  不再读 `bot`；A5/A10 也不再发 `bot=True`。A10 的 25/s 轻度信令因此**不再**被判成洪泛（符合其隐蔽设定）。
- **R16 A5 真因果**：洪泛改走 `AMF.start_registration()`（CoreBus + 队列 + 容量），
  `amf_overload` 由真实排队推导；受害车用 `core.reattach()` 摘网后重注册，拥塞期被 AMF 真拒绝、
  攻击结束后自动恢复入网与会话；`stop()` 清理合成 UE（含总线在途消息）。
- **R16 A6 真因果**：V2X 新增按 UE 的 `v2x.cpm.rx`，丢包率/额外时延由 `(5QI, GFBR)` 派生；
  删掉伪造的 `lost=true` CPM；`smf.degraded` 从此有真实消费者。
- **R9 A6 幅度生效**：`a → GFBR 1000−(800a) kbps、ingest 40+160a ms、制动 1.2+1.8a m/s²`；
  包络改 `gfbr_to<=400 kbps 且 ingest_delay>=100 ms`；`stop()` 恢复**攻击前**的 QoS 档位。
- **R10 A4 速率真实**：`Pacer` 按 `next_due` 补发，标称 8.4 Hz 实测 8.33 Hz（旧实现 16.4 Hz 只发 10 Hz）；
  `truth.params.actual_rate`、载荷 `rate_hz` 都用实测值。
- **R9/R13 A4 幅度与 target**：`a → 位置偏移 20–60 m`、速率 2–10 Hz；`spec.target` 命中已装备车辆时生效。
- **R11 A5 速率真实**：`truth.params.rate` 是到达速率、`actual_rate` 实测；平均速率由 `Pacer` 保证。
- **R1 A5 云控腿**：`token` 未就绪时挂起到下一 tick 再提交，不再静默跳过。
- **R17 observed 两个洞**：`Simulation` 对**外部传入的 sink** 也包 `CountingSink`（否则 `evidence_seen` 恒空）；
  证据基线改为在 `start()` **之前**快照（否则 start 内发出的证据被误判缺失）。

### 变更（口径/默认值）

- **NF 排队模型落地**（`docs/09 §4.4` 的公式）：`NetworkFunction.tick()/accept(ctx)/latency_ms`，
  `queue_depth` 按 `capacity·dt` 排空，`shed_at` 控制卸流水位（AMF 0.3，SMF 0.8）。
- **核心网容量按单路口场景标定**：AMF `5000→800` 注册/s、SMF `2000→600` 会话/s、`queue_capacity 1000→300`。
  原因见 `docs/09 §4.4`：单路口打不满 5000/s，而真打满时落盘量会让批量评估不可用。
- `hold_queue` 只抬“显示负载/时延”，不参与准入（A10 的语义负载不再导致真拒绝）。
- A4/A5/A6 的包络判据全部改用实测值（`docs/03 §5`）。
- 新增 trace 事件 `v2x.cpm.degraded`（A6）；`v2x.cpm.forged` 只剩 A7。

### 测试

- 新增 `tests/test_a456.py`（11 项：Pacer、A4 速率/幅度/target、A5 状态机/受害车拒绝与恢复/去标记、
  A6 ready/幅度/CPM 降级、observed 证据基线）；全量 `pytest` 通过（119 项）。

### 注意

- A5 单次 3 s 攻击约 2.5–3 万条记录（真注册消息的扇出），批量跑之前要评估落盘量；
- `docs/03 §6.1` 的 500+500 指标仍未重测，A1–A6 的机制都变过。

## 未发布（审核整改 A1–A3，2026-11-06）

### 修复（对应 docs/attacks/review-2026-11-06）

- **R8 A3 强度真实生效**：表观目标 TTC = `3.2 − 2.0×amplitude`；高于 ADAS 门限（2.45 s）时完全不篡改（包络外），
  注入减速度也随幅度变化。a=0.2/0.35 无效果，a≥0.45 稳定诱发 ADAS 误刹。
- **R2 取消真正收尾**：`cancel()` 只登记意图，下一 tick 用 `stop(ctx)` 摘 hook/恢复状态/记 `t_end`；
  重复取消返回 False；`stop()` 幂等。
- **R3 去真值参照**：`sensing.radar.track.pos_x/pos_y` 由本通道 `range+azimuth` 重建；
  `radar_range_bias` 改为**多站点位置互证**（同一 entity 两站差 >6 m、连续 3 帧），不再依赖同通道自证。
- **R14 A2 目标对齐**：先选 RSU，再在其服务范围内选装备+ADAS 的受害车；停止牌会真正进入受害车目标列表。
- **R15 A1 因果闭环**：`ControlLayer.recent_brakes` 持久化 → `v2x` 发 DENM；链路负载改为“最近 1 s 实际 DENM 数”；
  A1 按 `denm_rate` 真实发报，受害车被诱导制动后速率 ×2。
- **R17 真值 observed**：`TruthEvent.observed`（实际 `t_end`/`cancelled`/`evidence_seen`/`evidence_missing`），
  `TruthStore.close()` 重写以回填；`stages` 标注为 narrative。
- **R18**：A1/A3/A10 文档按代码校正；A3 `ramp_s` 记录真实渐变时长；`docs/02` A3 包络阈值修正；
  `MAX_ACTIVE=40` 生效（超限拒绝）。
- **R6 包络分组**：`evaluate_runs` 包络内计入分母、包络外单列 `out_of_envelope`。

### 修复（攻击结束后的“余波”）

- **`affected` 不再粘滞**：fleet 的 `affected` 改为“有攻击 flag，或在**有攻击在跑**时最近 5 s 内收到过制动指令”，
  攻击结束后立即归零；累计口径移到车辆状态卡片的 `affected_ever`。
  车辆状态卡片也只在“有攻击在跑 / 可归因到攻击”时创建，正常交通的 ADAS 制动不再新增卡片。
- **V2X 不再重放旧制动**：`recent_brakes` 是持久 deque，此前每 2 s 会被重新发成 DENM（攻击结束后链路仍有“余波”）；
  现在只消费最近 1 s 内的制动事件，攻击结束即停。

### 测试

- 新增 `tests/test_a3.py`（6 项）、`tests/test_lifecycle.py`（4 项）、`test_metrics.py` 包络分组用例；
  全量 `pytest` 通过（108 项）。

### 注意

- `docs/03 §6.1` 的 500+500 指标来自旧实现，已标注**待重测**；A1/A3 的强度口径与旧文档不可比。

## 未发布（A3 跨层链修订，2026-11-05）
，2026-11-05）

### 变更

- **A3 重构为真正的跨层链**：不再篡改“受害车自己的回波”，而是锁定“受害车 + 其前方的跟驰目标 + 受害车服务传感器”，
  篡改前车回波的 `range_m` 与 `azimuth_deg`（渐变，把前车“拉近”到表观 TTC≈1.6 s）。
- **目标列表改为“测距重建”**（`sensing.py`）：`sensing.objects` 的 x/y 由 `range+azimuth` 反算，而不是直接抄真值。
  这是 A3 能影响 ADAS 的前提；A1/A2 的告警基线实测不变（ghost 是注入的，真车只是多了测距噪声）。
- **A3 真实后果**：受害车 ADAS 用被拉近的位置算出错误 TTC，触发一次本不该有的 `reason=ttc` 制动；
  60% 处再注入 `decel = 1.0+1.5×frac`（≈1.9 m/s²）的伪指令，并压低回波 SNR、打 `masked`、发 `telemetry.mask`。
- **检测规则双向化**：`radar_range_bias` 由 `mean > 1.5` 改为 `|mean| > 1.5`，否则“拉近型”篡改整条漏检。
- **Trace**：A3 的 `lead` 纳入攻击归因（此前 radar 事件只认受害车）；新增 `telemetry.mask` 事件；
  A3 S3 的 `decel` 判别区间放宽到 0.8–2.5，避免新幅度下阶段永不匹配。
- **A3 真值**：`params` 增加 `lead/apparent_lon_m/spoof/bias_sign→spoof`，包络改为 `bias ≥ 3.0 m`，
  `causal_chain` 改为 `radar.echo → sensing.objects → adas:ttc_brake → control.command → telemetry:mask`。

### 测试

- 新增 `tests/test_a3.py`（6 项）：选目标、回波/点迹残差、**ADAS 误刹的因果对照**、伪指令幅度、掩盖记录、检测规则。
- 全量 `pytest` 通过（103 项）。

### 文档

- 重写 [A3 跨层隐蔽攻击](docs/attacks/A3-跨层隐蔽攻击.md)：新增“影响/因果链”说明与生效前提。
- 同步 `docs/01`（注入面表）、`docs/02`（剧本目录）、`docs/03`（包络与待重测提示）、`docs/07`（实现走读）。

## v0.3.0（未打 tag）

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
- **页面内空间清理**：运行产物页「清理历史数据…」弹窗，按范围（会话/数据集）、保留最近 N 项、早于 N 天、清空四项决策实时预览；后台删除 + 进度条，完成后刷新占用；受保护会话不可删；`/api/storage`、`/api/storage/plan`、`/api/storage/cleanup` 三个接口。
- **运行追踪 v1**：独立 `/trace` 页面、watch/trace 数据层、A1 S1–S5 检查点单步、SSE；见 [docs/trace/10](docs/trace/10-v1实现说明.md)。
- **运行追踪 v2 规划**：`TraceProfile`/`WatchProvider`、S0、增量 SSE、trace.jsonl/回放、origin 与 5G 核心网融合（W7）；见 [docs/trace/11](docs/trace/11-v2完善规划.md)。
- **`/trace` 核心网/平台 provider**：无攻击时也显示 `core:summary`、8 个核心网节点、`core:ues`、`platform:summary`；节点 health 直接进入 watch 卡片。
- **`/trace` 核心网/平台事件**：`core.*`/`app.*`/`defense.action` 进入右侧事件栏；按 100 ms 聚合，A5 的 bot 注册洪泛不会挤掉 A1/A4 事件；`step_once()` 在 core/platform 后调用 `after_layer`。
- **A5 单步追踪**：`TraceProfile`（A1/A5）+ S0–S5 检查点；S0 在 scheduler 后、S1/S2 在 core 后、S3 在 platform 后真正暂停；同一层边界可连续展示多个阶段。
- **`/trace` 核心网会话/流表**：`core:sessions`（SMF `session_list`）与 `core:flows`（UPF `flow_list`）有界表格，显示 UE、DNN、5QI、TEID、路由与状态。
- **`/trace` 平台 token/订阅表**：`platform:tokens`（IAM `token_list`）与 `platform:subscriptions`（`subscription_list`）有界表格，显示 token、租户、scopes、订阅事件与状态。
- **A1–A10 追踪 profile**：新增 A2/A3/A4/A6/A7/A8/A9/A10 的 `TraceProfile`；A2–A10 从 S0 开始，A1 保持 S1–S5；`/api/trace/capabilities.profiles` 返回全部 profile。
- **跨层事件桥**：A3/A10 的 `radar.echo/track`（按 tick 采样）、A4 的 `v2x.cam.clone`、A6/A7 的 `v2x.cpm.forged` 投影为 TraceEvent；`core.*`/`app.*` 事件保留 `provenance/vehicle_ids/5qi/route/actor/bot` 等 profile 判定字段。
- **profile 顺序约束修正**：`wait_for` 的参考阶段尚未出现时不再绕过顺序；A2 的链路干扰不会抢在注入制动之前暂停。
- **`/trace` 会话与注入**：追踪页顶栏增加开始/停止；A1–A10 注入改为“类型下拉 + 强度 + 时长 + 注入”条；运行中注入自动冻结，保证 S0 可单步。
- **`/trace` 中栏分组与合并卡**：中栏改为“总览/感知/车辆/核心网/平台/攻击”标签；4 个传感器合并为标签卡；核心网合并为“节点健康矩阵 + UE/会话/流表”；平台合并为“概览/Token/订阅”；左栏点击自动切组定位。
- **`/trace` 表格可读性**：数值列的表头与数值同边右对齐（ID 类列保持左对齐）、列宽按内容收紧、表头吸顶、斑马纹与整行悬停高亮，暗色无竖线时也能对上表头与数据列。
- **`/trace` CPM 摘要表**：新增全局 target `v2x:cpm`（V2X 标签 → `CPM · broadcast summary`），一行一个 sender 只保留**最新一条** CPM；显示 `object_count/carried/ghost_in_cpm/truncated/attack/flags(FORGED/DEGRADED/LOST)/delay_ms/cbr`。幽灵按 `object_id` 回查 `sensing.last_objects`，摘要截断（>12 条）标 `TRUNC`；CPM 不逐条进事件环，避免 30 条/s 挤掉攻击证据。
- **移除复位**：主控制台取消复位按钮、`/api/session/reset` 与 `SessionManager.reset`；复位原本等价于停止后按原参数开始，重跑改为“停止 → 开始”。
- **测试**：新增 A1–A10 profile、事件桥、capabilities 与“无攻击不产生雷达事件”测试；全量 **97 项 pytest 通过**。

### 文档

- [`docs/01-系统架构与数据流.md`](docs/01-系统架构与数据流.md) 扩充为字段级数据字典：
  时间/坐标基准、统一信封、实体×产出总览、逐流字段说明（含单位、生成方式、用途）、
  攻击改写对照、真值/告警/附属文件、消费者矩阵、数据模型已知边界。
- trace 设计文档同步到 v1.3：更新 [12 当前实现状态](docs/trace/12-当前实现状态.md)、A1–A10 profile/事件桥与 01–11 的已实现/未实现边界；增量 SSE/回放/origin/focus 仍未实现。
- 新增 [攻击实现解析（A1–A10）](docs/attacks/README.md)：A1 文档移入 `docs/attacks/`，补齐 A2–A10 的代码走读、注入点、下游消费、真值、检测、时序与边界。

### 修复

- A4 真值移除未实现的 `uu:ghost_attach` / `uu` 域；A4 只声明实际可观测的 `auth`、`v2x`。
- A1 改为选择“由目标 RSU 服务且装备 ADAS”的车辆，保证 `control` 域可观测。
- 记录 schema 升为 v2；版本号 → `0.3.0`。
- `sensing.frame.object_count` 改为与同帧 `sensing.objects` 实际条数一致（此前不含攻击注入项）。
- 基站传感器产生的记录实体前缀由 `rsu:` 改为 `gnb:`（`sensing.objects`/`sensing.frame`/`ghost_object` 告警）。
- 回波 `snr_db` 与检测概率口径统一：卡车增加 3 dB 处理增益（此前判决用了、记录没用）。
- **命名解耦**：分布式 `Trace` 类型改为 `SpanContext`，`ctx.trace` 改为 `ctx.span`；`record.trace_id/span_id` 字段保持不变，为 `/trace` 观测追踪预留命名边界。
- 批量/实验目录识别兼容没有 `meta.json` 的单次运行（按 `truth/events.jsonl` 判定），`out/data_doc` 一类目录不再漏扫。
- Web 会话的 `<run>.zip` 下载缓存随运行目录一并统计与删除，不再留下孤儿文件。

### 测试

- 82 项测试通过（新增清理策略、保护规则、zip 统计与 API 流程）。

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
