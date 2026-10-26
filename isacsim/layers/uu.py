"""Uu（蜂窝）层：附着、切换、会话与小区负载。"""
from __future__ import annotations

from collections import defaultdict

from ..config import SceneCfg
from ..core.rng import rng_for
from .common import is_equipped


class UuLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.rng = rng_for(scene.seed, "uu:events")
        self._attached: set[str] = set()
        self._next_ho: dict[str, float] = {}
        self._cell: dict[str, str] = defaultdict(lambda: "CELL-1")

    def tick(self, ctx) -> None:
        t = ctx.t
        step = ctx.step_s
        for v in ctx.vehicles.values():
            if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                continue
            if v.vid not in self._attached:
                self._attached.add(v.vid)
                ctx.emit("uu.attach", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "cell_id": self._cell[v.vid],
                    "event": "attach",
                    "rrc_state": "connected",
                })
                self._next_ho[v.vid] = t + float(self.rng.uniform(12.0, 35.0))
            elif t >= self._next_ho.get(v.vid, 1e9):
                old = self._cell[v.vid]
                new = "CELL-2" if old == "CELL-1" else "CELL-1"
                self._cell[v.vid] = new
                self._next_ho[v.vid] = t + float(self.rng.uniform(15.0, 40.0))
                ctx.emit("uu.handover", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "from_cell": old,
                    "to_cell": new,
                    "event": "handover",
                })
        if int(round(t * 2)) != int(round((t - step) * 2)):
            cell_load = min(1.0, len(self._attached) / 120.0)
            ctx.emit("uu.session", "MEC", "net:uu", {
                "cell_id": "CELL-1",
                "attached": len(self._attached),
                "cell_load": round(cell_load, 3),
                "ul_bytes": int(len(self._attached) * 1200),
            })
