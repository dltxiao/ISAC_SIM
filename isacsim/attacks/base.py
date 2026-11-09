"""攻击原语/剧本的公共基础设施。"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import SceneCfg
from ..core.rng import rng_for
from ..truth.model import OFFICIAL_TYPES, TruthEvent


@dataclass
class AttackSpec:
    type: str
    count: int = 1
    target: str | None = None
    amplitude: float = 0.5
    duration: float = 12.0
    start: float | None = None      # 仿真时刻；None 表示立即
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AttackSpec":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        if extra:
            data.setdefault("params", {}).update(extra)
        return cls(**data)


def stable_pick(items: list, seed: int, salt: str):
    if not items:
        return None
    idx = zlib.crc32(f"{seed}:{salt}".encode()) % len(items)
    return items[idx]


def pick_vehicle(ctx, seed: int, salt: str = ""):
    vids = sorted(ctx.vehicles)
    if not vids:
        return None
    vid = stable_pick(vids, seed, salt)
    return ctx.vehicles.get(vid)


def pick_rsu(scene: SceneCfg, ctx, seed: int):
    rsus = [s for s in scene.sensors if s.kind == "rsu"]
    if not rsus:
        rsus = list(scene.sensors)
    return stable_pick(rsus, seed, "rsu")


def ahead(veh, dist: float, lateral: float = 0.0) -> tuple[float, float]:
    h = math.radians(veh.heading_deg)
    return (veh.x + math.cos(h) * dist - math.sin(h) * lateral,
            veh.y + math.sin(h) * dist + math.cos(h) * lateral)


class Pacer:
    """按“应发时刻”补发，避免浮点门控把速率量化掉（审核 R10/R11）。

    用法::

        self._pacer = Pacer(rate_hz=16.4)
        n = self._pacer.due(ctx.t, max_burst=2)
    """

    __slots__ = ("period", "next_due", "started", "sent")

    def __init__(self, rate_hz: float) -> None:
        self.period = 1.0 / max(1e-6, float(rate_hz))
        self.next_due: float | None = None
        self.started = False
        self.sent = 0

    def set_rate(self, rate_hz: float) -> None:
        self.period = 1.0 / max(1e-6, float(rate_hz))
        self.started = False
        self.next_due = None

    def due(self, t: float, max_burst: int = 1) -> int:
        """返回本 tick 应发条数（0..max_burst）。"""
        if not self.started:
            self.started = True
            self.next_due = t
        if self.next_due is None or t + 1e-9 < self.next_due:
            return 0
        n = 0
        while t + 1e-9 >= self.next_due and n < max_burst:
            n += 1
            self.next_due += self.period
        # 落后太多（长时间暂停）时不要一次性补爆
        if t - self.next_due > 1.0:
            self.next_due = t + self.period
        self.sent += n
        return n

    def actual_hz(self, elapsed: float) -> float:
        return round(self.sent / elapsed, 2) if elapsed > 0 else 0.0


def ghost_object(veh, oid: int, dist: float = 30.0, cls: str = "obstacle",
                 lateral: float = 0.0) -> dict[str, Any]:
    x, y = ahead(veh, dist, lateral)
    return {
        "object_id": oid,
        "cls": cls,
        "x": round(x, 3),
        "y": round(y, 3),
        "z": 0.8,
        "l": 4.5 if cls == "car" else 0.6,
        "w": 1.8 if cls == "car" else 0.6,
        "h": 1.6 if cls == "car" else 1.8,
        "vx": 0.0,
        "vy": 0.0,
        "heading_deg": round(veh.heading_deg, 1),
        "confidence": 0.92,
        "tracked_age": 50,
        "occluded": False,
        "entity": f"ghost:{oid}",
    }


class AttackInstance:
    atype = "?"
    label = "攻击"
    is_campaign = True

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        self.spec = spec
        self.scene = scene
        self.seed = seed
        self.id = attack_id
        self.rng = rng_for(seed, f"attack:{attack_id}")
        self.t0: float = 0.0
        self.t1: float = 0.0
        self.started = False
        self.finished = False
        self.cancel_requested = False
        self.t_end: float | None = None
        self.start_at: float = -1.0
        self.submitted_t: float | None = None
        self.offset: float = 0.0
        self.queued = True
        self.kind = self.atype
        self._hooks: list[tuple[str, Callable]] = []
        self.extra: dict[str, Any] = {}
        self.blocked_reason: str = ""
        self.aborted: str = ""

    # ---- 生命周期 -------------------------------------------------------
    def ready(self, ctx) -> tuple[bool, str]:
        """前置条件检查：返回 (就绪, 未就绪原因)。

        未就绪时调度器会把实例保持在排队状态，最多等 `READY_TIMEOUT_S`；
        超时则 `abort()` 并写入带 `aborted` 的真相，而不是静默 `finished`。
        """
        return True, ""

    def abort(self, ctx, reason: str) -> None:
        """前置条件始终不满足：落地一条“已中止”的真相，便于评估剔除。"""
        self.started = True
        self.aborted = reason or "precondition"
        self.t0 = ctx.t
        self.t1 = ctx.t
        self.finished = True

    def start(self, ctx) -> None:
        self.started = True
        self.t0 = ctx.t
        self.t1 = self.t0 + self.spec.duration

    def tick(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        """幂等收尾：摘 hook、记录实际结束时刻；重复调用无副作用。"""
        if self.finished:
            return
        for stream, fn in self._hooks:
            ctx.hooks.off(stream, fn)
        self._hooks.clear()
        self.t_end = ctx.t
        ev = getattr(self, "truth_event", None)
        if ev is not None and ctx.stats is not None:
            base = getattr(self, "_stream_base", {}) or {}
            counts = getattr(ctx.stats, "stream_records", {})
            seen, missing = [], []
            for name in ev.expected_evidence or []:
                (seen if counts.get(name, 0) > base.get(name, 0) else missing).append(name)
            ev.observed = {
                "t_end": round(ctx.t, 3),
                "cancelled": bool(self.cancel_requested),
                "aborted": self.aborted or None,
                "blocked_reason": (self.blocked_reason or None) if self.aborted else None,
                "evidence_seen": seen,
                "evidence_missing": missing,
                "stages_role": "narrative",
            }
        self.finished = True

    # ---- 工具 -----------------------------------------------------------
    def hook(self, ctx, stream: str, fn: Callable) -> None:
        ctx.hooks.on(stream, fn)
        self._hooks.append((stream, fn))

    def truth(self) -> TruthEvent:
        return TruthEvent(
            event_id=f"EVT-{self.id}",
            campaign=self.atype,
            t0=self.t0,
            t1=self.t1,
            seed=self.seed,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.atype,
            "label": self.label,
            "kind": "campaign" if self.is_campaign else "primitive",
            "t0": round(self.t0, 2),
            "t1": round(self.t1, 2),
            "started": self.started,
            "finished": self.finished,
            "cancelled": bool(getattr(self, "cancel_requested", False)),
            "aborted": self.aborted or None,
            "blocked_reason": self.blocked_reason or None,
            "t_end": round(self.t_end, 2) if self.t_end is not None else None,
            "amplitude": self.spec.amplitude,
            "target": self.spec.target,
            "params": self.spec.params,
        }
