"""边端聚合与实体图快照（供 STGNN / 攻击链聚合使用）。"""
from __future__ import annotations

from ..config import SceneCfg
from ..world.geometry import distance, in_fov, bearing_deg


class EdgeLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene

    def tick(self, ctx) -> None:
        t = ctx.t
        if int(round(t * 10)) == int(round((t - ctx.step_s) * 10)):
            return
        vehicles = list(ctx.vehicles.values())
        nodes = []
        for v in vehicles:
            nodes.append({"id": f"veh:{v.vid}", "kind": "vehicle",
                          "x": round(v.x, 2), "y": round(v.y, 2),
                          "cls": getattr(v, "cls", "car")})
        for s in self.scene.sensors:
            nodes.append({"id": f"{s.kind}:{s.id}", "kind": s.kind,
                          "x": s.x, "y": s.y})
        edges = []
        for s in self.scene.sensors:
            if s.kind != "rsu":
                continue
            for v in vehicles:
                d = distance(v.x, v.y, s.x, s.y)
                if d <= s.range_m and in_fov(bearing_deg(v.x - s.x, v.y - s.y),
                                              s.yaw_deg, s.fov_deg):
                    edges.append({"src": f"veh:{v.vid}", "dst": f"rsu:{s.id}",
                                  "kind": "coverage", "w": round(1.0 - d / s.range_m, 3)})
        for v in vehicles:
            edges.append({"src": f"veh:{v.vid}", "dst": "net:uu",
                          "kind": "uu", "w": 0.5})
        ctx.emit("graph.snapshot", "MEC", "graph:scene", {
            "t": round(t, 3),
            "nodes": nodes,
            "edges": edges[:600],
        })
