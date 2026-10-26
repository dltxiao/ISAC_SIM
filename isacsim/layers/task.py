"""任务层：航路点、阶段、时限与偏差。"""
from __future__ import annotations

from ..config import SceneCfg


class TaskLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene

    def tick(self, ctx) -> None:
        t = ctx.t
        if int(t) == int(t - ctx.step_s):
            return
        for v in ctx.vehicles.values():
            phase = "braking" if getattr(v, "speed", 0.0) < 5.0 else "cruise"
            ctx.emit("task.state", v.vid, f"veh:{v.vid}", {
                "task_id": f"TASK-{v.vid}",
                "node_id": v.vid,
                "task_type": "city_cruise",
                "phase": phase,
                "waypoint_idx": int(v.x // 50),
                "expected_route": "ROUTE-EAST" if v.vx >= 0 else "ROUTE-WEST",
                "deviation_m": round(abs(abs(v.vx) - 15.0), 2),
                "deadline_s": 600.0,
            })
