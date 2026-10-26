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
        self.start_at: float = -1.0
        self.queued = True
        self.kind = self.atype
        self._hooks: list[tuple[str, Callable]] = []
        self.extra: dict[str, Any] = {}

    # ---- 生命周期 -------------------------------------------------------
    def start(self, ctx) -> None:
        self.started = True
        self.t0 = ctx.t
        self.t1 = self.t0 + self.spec.duration

    def tick(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        for stream, fn in self._hooks:
            ctx.hooks.off(stream, fn)
        self._hooks.clear()
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
            "amplitude": self.spec.amplitude,
            "target": self.spec.target,
            "params": self.spec.params,
        }
