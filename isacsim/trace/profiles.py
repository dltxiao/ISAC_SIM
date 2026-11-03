"""TraceProfile：攻击剧本的检查点描述。

第一版只做“声明式阶段 + 事件匹配”，不执行任意表达式：
- A1：S1–S5 + END（保持既有行为，不新增 S0）；
- A5：S0（攻击启动）/S1–S5 + END；
- A2/A3/A4/A6/A7/A8/A9/A10：S0–S4/S5 + END；
- 未注册攻击使用空 profile（只按 MAX 推进）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class StageSpec:
    id: str
    title: str
    kinds: tuple[str, ...]
    pause_at: str = "after_tick"
    predicate: Callable[[dict], bool] | None = field(default=None, compare=False)
    # 可选阶段：例如 A1 的 S4 不出现时，S5 最多等 0.5 s。
    wait_for: tuple[str, ...] = ()
    wait_ref: str | None = None
    wait_s: float = 0.0

    def matches(self, event: dict) -> bool:
        if event.get("kind") not in self.kinds:
            return False
        if self.predicate is not None:
            try:
                return bool(self.predicate(event))
            except Exception:  # noqa: BLE001 - profile 不得中断仿真
                return False
        return True


@dataclass(frozen=True)
class TraceProfile:
    id: str
    attack_types: tuple[str, ...]
    stages: tuple[StageSpec, ...]

    def stage(self, stage_id: str) -> StageSpec | None:
        for item in self.stages:
            if item.id == stage_id:
                return item
        return None


def _result_in(event: dict, values: tuple[str, ...]) -> bool:
    return str((event.get("data") or {}).get("result")) in values


def _data(event: dict) -> dict:
    return event.get("data") or {}


def _int_value(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _a2_stop_sign(event: dict) -> bool:
    for ghost in _data(event).get("ghosts") or []:
        if isinstance(ghost, dict) and ghost.get("cls") == "stop_sign":
            return True
    return False


def _a2_brake(event: dict) -> bool:
    data = _data(event)
    return data.get("reason") == "injected" or str(data.get("src", "")).startswith("attacker:")


def _a3_brake(event: dict) -> bool:
    data = _data(event)
    if data.get("reason") != "injected":
        return False
    return _float_value(data.get("decel"), 0.0) <= 1.5


def _a4_cert(event: dict) -> bool:
    return _data(event).get("stream") == "auth.cert"


def _a4_fingerprint(event: dict) -> bool:
    return _data(event).get("stream") == "auth.fingerprint"


def _a6_qos(event: dict) -> bool:
    data = _data(event)
    return data.get("msg_type") == "qos_flow_modify" and _int_value(data.get("5qi"), 99) <= 9


def _a6_slow_ingest(event: dict) -> bool:
    return _float_value(_data(event).get("latency_ms"), 0.0) >= 50.0


def _a7_route(event: dict) -> bool:
    data = _data(event)
    return (data.get("msg_type") == "route_change"
            and data.get("route") not in (None, "", "dn:DN-01"))


def _a7_forged_ingest(event: dict) -> bool:
    data = _data(event)
    if event.get("kind") == "v2x.cpm.forged":
        return bool(data.get("forged") or data.get("degraded"))
    return str(data.get("provenance") or "") not in ("", "dn:DN-01")


def _a8_nef_unsubscribe(event: dict) -> bool:
    data = _data(event)
    return data.get("msg_type") == "api_unsubscribe" and data.get("result") == "accept"


def _a8_subscription_revoke(event: dict) -> bool:
    data = _data(event)
    return data.get("msg_type") == "unsubscribe" or data.get("action") == "revoke"


def _a8_dropped(event: dict) -> bool:
    data = _data(event)
    return data.get("cause") == "no_subscription" or data.get("result") == "dropped"


def _a9_token_reuse(event: dict) -> bool:
    data = _data(event)
    return data.get("msg_type") == "token_validate" and data.get("result") == "accept"


def _a9_batch_control(event: dict) -> bool:
    data = _data(event)
    vehicles = data.get("vehicle_ids") or []
    return data.get("result") == "accept" and len(vehicles) >= 3


def _a9_cloud_control(event: dict) -> bool:
    return _data(event).get("reason") == "cloud_control"


def _a10_signaling(event: dict) -> bool:
    data = _data(event)
    return bool(data.get("bot")) or data.get("result") == "reject"


def _a10_audit(event: dict) -> bool:
    return str(_data(event).get("actor", "")).startswith("attacker:")


def _a5_s1(event: dict) -> bool:
    data = event.get("data") or {}
    return (
        data.get("msg_type") in ("amf_overload", "registration_reject")
        or _result_in(event, ("reject", "enter"))
    )


def _a5_s2(event: dict) -> bool:
    data = event.get("data") or {}
    return (
        data.get("msg_type") in ("pdu_session_establish_reject", "pdu_session_reject")
        or _result_in(event, ("reject",))
    )


def _a5_s3(event: dict) -> bool:
    return event.get("kind") in ("app.ingest", "app.control")


A1_PROFILE = TraceProfile(
    id="a1",
    attack_types=("A1",),
    stages=(
        StageSpec("S1", "目标列表被篡改", ("ghost.added",)),
        StageSpec("S2", "ADAS 误判制动", ("control.command",)),
        StageSpec("S3", "通信负载上升", ("pc5.rise",)),
        StageSpec("S4", "物理后果：车辆减速", ("vehicle.speed_drop",)),
        StageSpec(
            "S5", "检测器告警", ("alert",),
            wait_for=("S4",), wait_ref="S2", wait_s=0.5,
        ),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A2_PROFILE = TraceProfile(
    id="a2",
    attack_types=("A2",),
    stages=(
        StageSpec("S0", "攻击启动：伪造停止牌", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "目标列表出现停止牌", ("ghost.added",),
                  predicate=_a2_stop_sign),
        StageSpec("S2", "向目标车注入制动", ("control.command",),
                  predicate=_a2_brake,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "PC5 链路干扰", ("pc5.rise",),
                  wait_for=("S2",), wait_ref="S2", wait_s=0.1),
        StageSpec("S4", "车辆急刹/减速", ("vehicle.speed_drop",),
                  wait_for=("S3",), wait_ref="S2", wait_s=0.5),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S2", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A3_PROFILE = TraceProfile(
    id="a3",
    attack_types=("A3",),
    stages=(
        StageSpec("S0", "隐蔽偏置开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "雷达回波距离开始偏移", ("radar.echo",)),
        StageSpec("S2", "点迹随回波偏移", ("radar.track",),
                  wait_for=("S1",), wait_ref="S1", wait_s=0.2),
        StageSpec("S3", "延迟注入小幅制动", ("control.command",),
                  predicate=_a3_brake,
                  wait_for=("S1", "S2"), wait_ref="S1", wait_s=1.0),
        StageSpec("S4", "物理后果：轻微减速", ("vehicle.speed_drop",),
                  wait_for=("S3",), wait_ref="S3", wait_s=0.5),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S3", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A4_PROFILE = TraceProfile(
    id="a4",
    attack_types=("A4",),
    stages=(
        StageSpec("S0", "仿冒开始：克隆标识/指纹", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "克隆通信标识", ("record.clone",),
                  predicate=_a4_cert),
        StageSpec("S2", "复制信道指纹", ("record.clone",),
                  predicate=_a4_fingerprint,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.2),
        StageSpec("S3", "伪标识 CAM 注入", ("v2x.cam.clone",),
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "位置跳变告警", ("alert",),
                  wait_for=("S3",), wait_ref="S3", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A5_PROFILE = TraceProfile(
    id="a5",
    attack_types=("A5",),
    stages=(
        StageSpec("S0", "信令风暴开始", ("attack.start",), pause_at="after_scheduler"),
        StageSpec("S1", "AMF 过载/拒绝", ("core.nas",),
                  pause_at="after_layer:core", predicate=_a5_s1),
        StageSpec("S2", "PDU 会话失败", ("core.session",),
                  pause_at="after_layer:core", predicate=_a5_s2),
        StageSpec("S3", "平台 Ingest/云控", ("app.ingest", "app.control"),
                  pause_at="after_layer:platform", predicate=_a5_s3,
                  wait_for=("S1", "S2"), wait_ref="S1", wait_s=1.0),
        StageSpec("S4", "远控/物理后果", ("control.command", "vehicle.speed_drop"),
                  wait_for=("S1", "S2", "S3"), wait_ref="S1", wait_s=2.0),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S1", wait_s=3.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A6_PROFILE = TraceProfile(
    id="a6",
    attack_types=("A6",),
    stages=(
        StageSpec("S0", "QoS 抢占开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "5QI 79→9", ("core.qos",),
                  pause_at="after_layer:core", predicate=_a6_qos),
        StageSpec("S2", "CPM 降级/伪造", ("v2x.cpm.forged",),
                  pause_at="after_layer:platform",
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "平台 Ingest 延迟", ("app.ingest",),
                  pause_at="after_layer:platform", predicate=_a6_slow_ingest,
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "云控制动", ("control.command",),
                  wait_for=("S3",), wait_ref="S1", wait_s=0.5),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S1", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A7_PROFILE = TraceProfile(
    id="a7",
    attack_types=("A7",),
    stages=(
        StageSpec("S0", "UPF/N6 劫持开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "UPF 路由改向", ("core.upf",),
                  pause_at="after_layer:core", predicate=_a7_route),
        StageSpec("S2", "伪造 CPM 入平台", ("v2x.cpm.forged", "app.ingest"),
                  pause_at="after_layer:platform", predicate=_a7_forged_ingest,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "云控制动", ("control.command",),
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "溯源/制动告警", ("alert",),
                  wait_for=("S3",), wait_ref="S1", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A8_PROFILE = TraceProfile(
    id="a8",
    attack_types=("A8",),
    stages=(
        StageSpec("S0", "订阅篡改开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "NEF 退订 DENM/SPaT", ("core.nef",),
                  pause_at="after_layer:core", predicate=_a8_nef_unsubscribe),
        StageSpec("S2", "平台订阅撤销", ("app.subscription",),
                  pause_at="after_layer:platform", predicate=_a8_subscription_revoke,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "平台漏事件", ("app.ingest",),
                  pause_at="after_layer:platform", predicate=_a8_dropped,
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "云控制动", ("control.command",),
                  wait_for=("S3",), wait_ref="S1", wait_s=0.5),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S1", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A9_PROFILE = TraceProfile(
    id="a9",
    attack_types=("A9",),
    stages=(
        StageSpec("S0", "平台越权开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "复用合法租户 Token", ("app.auth",),
                  pause_at="after_layer:platform", predicate=_a9_token_reuse),
        StageSpec("S2", "批量云控下发", ("app.control",),
                  pause_at="after_layer:platform", predicate=_a9_batch_control,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "控制指令到达车辆", ("control.command",),
                  predicate=_a9_cloud_control,
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "多车减速", ("vehicle.speed_drop",),
                  wait_for=("S3",), wait_ref="S1", wait_s=1.0),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S1", wait_s=1.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


A10_PROFILE = TraceProfile(
    id="a10",
    attack_types=("A10",),
    stages=(
        StageSpec("S0", "隐蔽组合开始", ("attack.start",),
                  pause_at="after_scheduler"),
        StageSpec("S1", "雷达回波渐偏", ("radar.echo",),
                  pause_at="after_layer:core"),
        StageSpec("S2", "轻度注册信令", ("core.nas",),
                  pause_at="after_layer:core", predicate=_a10_signaling,
                  wait_for=("S1",), wait_ref="S1", wait_s=0.5),
        StageSpec("S3", "审计抑制", ("app.audit",),
                  pause_at="after_layer:platform", predicate=_a10_audit,
                  wait_for=("S2",), wait_ref="S1", wait_s=0.5),
        StageSpec("S4", "小幅制动", ("control.command",),
                  wait_for=("S3",), wait_ref="S1", wait_s=0.5),
        StageSpec("S5", "检测器告警", ("alert",),
                  wait_for=("S4",), wait_ref="S1", wait_s=3.0),
        StageSpec("END", "攻击结束", ("attack.end",)),
    ),
)


DEFAULT_PROFILE = TraceProfile(id="default", attack_types=(), stages=())

# 保持 A1–A10 的展示顺序；dict 顺序同时用于 capabilities。
PROFILES = {
    "A1": A1_PROFILE,
    "A2": A2_PROFILE,
    "A3": A3_PROFILE,
    "A4": A4_PROFILE,
    "A5": A5_PROFILE,
    "A6": A6_PROFILE,
    "A7": A7_PROFILE,
    "A8": A8_PROFILE,
    "A9": A9_PROFILE,
    "A10": A10_PROFILE,
}


def profile_for(atype: str | None) -> TraceProfile:
    return PROFILES.get(str(atype or ""), DEFAULT_PROFILE)


def profile_ids() -> list[str]:
    return list(PROFILES)
