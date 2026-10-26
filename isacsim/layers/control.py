"""控制层：ADAS（TTC/AEB）消费"被篡改后的感知"，并通过执行器回写 SUMO。

这一层是因果闭环的关键：
感知注入 → 目标列表出现危险目标 → TTC 触发 → 制动指令 → SUMO 中真实减速/排队。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import SceneCfg
from ..core.rng import rng_for
from .common import has_adas, is_equipped, serving_sensor


@dataclass
class PendingBrake:
    due_t: float
    vid: str
    decel: float
    duration: float


class ControlLayer:
    TTC_THRESHOLD = 2.5
    REACTION_S = 0.12
    DECEL = 3.0

    def __init__(self, scene: SceneCfg, sensing=None) -> None:
        self.scene = scene
        self.sensing = sensing
        self.rng = rng_for(scene.seed, "control:adas")
        self.pending: list[PendingBrake] = []
        self.last_cmd_t: dict[str, float] = {}
        self._seq: dict[str, int] = {}
        self._braking_until: dict[str, float] = {}

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        t = ctx.t
        self._apply_pending(ctx, t)

        vehicles = ctx.vehicles
        for v in vehicles.values():
            if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                continue
            if not has_adas(v.vid, self.scene.vehicles.adas_ratio):
                continue
            if v.speed < 2.0:
                continue
            if t - self.last_cmd_t.get(v.vid, -1e9) < 3.0:
                continue
            sensor = serving_sensor(v, [s for s in self.scene.sensors if s.kind in ("rsu", "gnb")])
            if sensor is None:
                continue
            objects = (self.sensing.last_objects.get(sensor.id, [])
                       if self.sensing is not None else [])
            hazard = self._find_hazard(v, objects)
            if hazard is None:
                continue
            ttc, obj = hazard
            if ttc >= self.TTC_THRESHOLD:
                continue
            self._command(ctx, v, ttc, reason="ttc", source=f"adas:{v.vid}")

    # ------------------------------------------------------------------
    def _find_hazard(self, veh, objects: list[dict]) -> tuple[float, dict] | None:
        hx, hy = math.cos(math.radians(veh.heading_deg)), math.sin(math.radians(veh.heading_deg))
        px, py = -hy, hx
        best: tuple[float, dict] | None = None
        for o in objects:
            dx, dy = o["x"] - veh.x, o["y"] - veh.y
            longitudinal = dx * hx + dy * hy
            lateral = dx * px + dy * py
            if not (2.0 < longitudinal < 60.0):
                continue
            if abs(lateral) > 2.2:
                continue
            closing = veh.speed - (o.get("vx", 0.0) * hx + o.get("vy", 0.0) * hy)
            closing = max(closing, 0.2)
            ttc = longitudinal / closing
            if best is None or ttc < best[0]:
                best = (ttc, o)
        return best

    def _command(self, ctx, veh, ttc: float, reason: str, source: str,
                 *, decel: float | None = None, duration: float = 1.2) -> None:
        t = ctx.t
        self.last_cmd_t[veh.vid] = t
        self._seq[veh.vid] = self._seq.get(veh.vid, 0) + 1
        dec = self.DECEL if decel is None else decel
        ctx.emit("control.command", source, f"veh:{veh.vid}", {
            "src": source,
            "dst": veh.vid,
            "cmd_type": "brake",
            "params": {"decel_mps2": dec, "ttc_s": round(ttc, 3)},
            "seq": self._seq[veh.vid],
            "sign_status": "valid",
            "reason": reason,
            "apply_after_ms": int(self.REACTION_S * 1000),
        })
        self.pending.append(PendingBrake(due_t=t + self.REACTION_S, vid=veh.vid,
                                         decel=dec, duration=duration))
        ctx.extras.setdefault("brake_events", []).append({
            "vid": veh.vid, "x": veh.x, "y": veh.y,
            "cause": "emergency_brake" if reason != "injected" else "injected_brake",
        })
        ctx.extras["emergency_until"] = max(ctx.extras.get("emergency_until", 0.0), t + 3.0)

    def _apply_pending(self, ctx, t: float) -> None:
        keep: list[PendingBrake] = []
        for p in self.pending:
            if p.due_t > t:
                keep.append(p)
                continue
            veh = ctx.vehicles.get(p.vid)
            if veh is None:
                continue
            target = max(0.0, veh.speed - p.decel * p.duration)
            if ctx.sumo is not None:
                ctx.sumo.slow_down(p.vid, target, p.duration)
            self._braking_until[p.vid] = t + p.duration
        self.pending = keep

    def inject_brake(self, ctx, vid: str, decel: float = 4.5, duration: float = 1.5) -> None:
        """攻击原语：直接向目标车注入制动指令（A2 控制指令误导）。"""
        veh = ctx.vehicles.get(vid)
        if veh is None:
            return
        self._command(ctx, veh, ttc=99.0, reason="injected",
                      source=f"attacker:ctl-{vid}", decel=decel, duration=duration)
