"""边端聚合与实体图快照（供 STGNN / 攻击链聚合使用）。

v2 增加 RAN/core 层节点与边；按层采样，避免 600 条边上限把核心网边截掉。
"""
from __future__ import annotations

from ..config import SceneCfg
from ..world.geometry import bearing_deg, distance, in_fov


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
            nodes.append({"id": f"veh:{v.vid}", "kind": "vehicle", "layer": "edge",
                          "x": round(v.x, 2), "y": round(v.y, 2),
                          "cls": getattr(v, "cls", "car")})
        for s in self.scene.sensors:
            nodes.append({"id": f"{s.kind}:{s.id}", "kind": s.kind,
                          "layer": "ran" if s.kind == "gnb" else "edge",
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
        for s in self.scene.sensors:
            if s.kind != "gnb":
                continue
            for v in vehicles:
                d = distance(v.x, v.y, s.x, s.y)
                if d <= s.range_m and in_fov(bearing_deg(v.x - s.x, v.y - s.y),
                                              s.yaw_deg, s.fov_deg):
                    edges.append({"src": f"veh:{v.vid}", "dst": f"gnb:{s.id}",
                                  "kind": "radio", "w": round(1.0 - d / s.range_m, 3)})
        for v in vehicles:
            edges.append({"src": f"veh:{v.vid}", "dst": "net:uu",
                          "kind": "uu", "w": 0.5})

        core = ctx.extras.get("core") or ctx.extras.get("layers", {}).get("core")
        if core is not None:
            nodes.extend(core.graph_nodes())
            edges.extend(core.graph_edges())
        platform = ctx.extras.get("platform") or ctx.extras.get("layers", {}).get("platform")
        if platform is not None:
            nodes.extend(platform.graph_nodes())
            edges.extend(platform.graph_edges())

        ctx.emit("graph.snapshot", "MEC", "graph:scene", {
            "t": round(t, 3),
            "nodes": nodes,
            "edges": edges[:600],
        })
