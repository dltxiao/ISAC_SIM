"""Uu（蜂窝）层：基于 gNB 覆盖的附着、切换与会话。

与早期的“定时器翻转 CELL-1/2”不同，本实现由 gNB 的几何覆盖驱动：
- 只有进入 gNB 覆盖/FOV 的车辆才附着；
- serving gNB 变化触发切换；
- 会话事件带 pdu_session_id / 5qi / latency_ms，供 CoreLayer 消费。
"""
from __future__ import annotations

from ..config import SceneCfg, SensorCfg
from ..core.rng import rng_for
from ..world.geometry import bearing_deg, distance, in_fov
from .common import is_equipped


class UuLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.rng = rng_for(scene.seed, "uu:events")
        self.gnbs: list[SensorCfg] = [s for s in scene.sensors if s.kind == "gnb"]
        # vid -> serving gNB id；CoreLayer 读取该映射做注册/切换
        self.attached: dict[str, str] = {}
        self._last_session: dict[str, float] = {}
        self._session_seq: dict[str, int] = {}

    # ------------------------------------------------------------------
    def serving_gnb(self, veh) -> SensorCfg | None:
        best: tuple[float, SensorCfg] | None = None
        for gnb in self.gnbs:
            d = distance(veh.x, veh.y, gnb.x, gnb.y)
            if d > gnb.range_m:
                continue
            if not in_fov(bearing_deg(veh.x - gnb.x, veh.y - gnb.y),
                          gnb.yaw_deg, gnb.fov_deg):
                continue
            if best is None or d < best[0]:
                best = (d, gnb)
        return best[1] if best else None

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        t = ctx.t
        for v in ctx.vehicles.values():
            if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                continue
            gnb = self.serving_gnb(v)
            if gnb is None:
                old = self.attached.pop(v.vid, None)
                if old is not None:
                    ctx.emit("uu.attach", v.vid, f"veh:{v.vid}", {
                        "node_id": v.vid,
                        "event": "detach",
                        "gnb_id": old,
                        "cell_id": f"{old}-CELL",
                        "rrc_state": "idle",
                    })
                continue

            old = self.attached.get(v.vid)
            if old is None:
                self.attached[v.vid] = gnb.id
                self._session_seq[v.vid] = 1
                self._last_session[v.vid] = -1e9
                ctx.emit("uu.attach", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "event": "attach",
                    "gnb_id": gnb.id,
                    "cell_id": f"{gnb.id}-CELL",
                    "rrc_state": "connected",
                })
            elif old != gnb.id:
                self.attached[v.vid] = gnb.id
                ctx.emit("uu.handover", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "from_gnb": old,
                    "to_gnb": gnb.id,
                    "from_cell": f"{old}-CELL",
                    "to_cell": f"{gnb.id}-CELL",
                    "event": "handover",
                    "interruption_ms": round(float(self.rng.uniform(8.0, 25.0)), 2),
                })

            # 会话状态 0.5 Hz，带 QoS 信息，供 core/platform 关联
            if t - self._last_session.get(v.vid, -1e9) >= 2.0:
                self._last_session[v.vid] = t
                seq = self._session_seq.get(v.vid, 0)
                self._session_seq[v.vid] = seq + 1
                d = distance(v.x, v.y, gnb.x, gnb.y)
                ctx.emit("uu.session", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "gnb_id": gnb.id,
                    "cell_id": f"{gnb.id}-CELL",
                    "pdu_session_id": f"PS-{v.vid}",
                    "session_seq": seq,
                    "5qi": 79,
                    "latency_ms": round(2.0 + d / 100.0, 2),
                    "attached": len(self.attached),
                })
