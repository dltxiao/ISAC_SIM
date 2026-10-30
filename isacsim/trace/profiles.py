"""TraceProfile：攻击剧本的检查点描述。

第一版只做“声明式阶段 + 事件匹配”，不执行任意表达式：
- A1：S1–S5 + END；
- A5：S0（攻击启动）/S1–S5 + END；
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


DEFAULT_PROFILE = TraceProfile(id="default", attack_types=(), stages=())

PROFILES = {
    "A1": A1_PROFILE,
    "A5": A5_PROFILE,
}


def profile_for(atype: str | None) -> TraceProfile:
    return PROFILES.get(str(atype or ""), DEFAULT_PROFILE)
