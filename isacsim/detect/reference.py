"""参考检测器：分域规则 + 跨层关联。

明确边界：这是用于验证"环境是否可用、指标是否可达"的基线，
不是项目最终交付的检测模型；它只读取观测流，不读取 truth。
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .. import T0_NS
from ..config import SceneCfg


@dataclass
class Alert:
    t: float
    rule: str
    entity: str
    detail: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 3), "rule": self.rule, "entity": self.entity,
                "detail": self.detail, "latency_ms": round(self.latency_ms, 3)}


class ReferenceDetector:
    def __init__(self, scene: SceneCfg, stats=None) -> None:
        self.scene = scene
        self.stats = stats
        self.t = 0.0
        self.alerts: list[Alert] = []
        self._last_objs: dict[str, list[dict]] = {}
        self._last_cam: dict[str, dict] = {}
        self._cam_track: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        self._obj_streak: dict[tuple[str, int], int] = defaultdict(int)
        self._bias_streak: dict[tuple[str, int], int] = defaultdict(int)
        self._resid: dict[tuple[str, int], deque] = defaultdict(lambda: deque(maxlen=6))
        self._cbr_hist: deque = deque(maxlen=30)
        self._gnss: dict[str, tuple[float, float, float]] = {}
        self._last_alert: dict[str, float] = {}
        self.static_map = {(round(o["x"], 1), round(o["y"], 1)) for o in scene.static_objects}

    # ------------------------------------------------------------------
    def _alert(self, rule: str, entity: str, detail: dict[str, Any],
               wall_ns: int | None = None) -> None:
        now = self.t
        if now - self._last_alert.get(f"{rule}:{entity}", -1e9) < 1.0:
            return
        self._last_alert[f"{rule}:{entity}"] = now
        latency = 0.0
        if wall_ns:
            latency = (time.monotonic_ns() - wall_ns) / 1e6
        self.alerts.append(Alert(t=now, rule=rule, entity=entity,
                                 detail=detail, latency_ms=latency))
        if self.stats is not None:
            self.stats.add_alert(rule, entity, latency)

    # ------------------------------------------------------------------
    def on_record(self, rec) -> None:
        stream = rec.stream
        p = rec.payload
        self.t = max(self.t, (rec.ts_ns - T0_NS) / 1e9)
        if stream == "sensing.objects":
            self._on_objects(rec, p)
        elif stream == "sensing.radar.track":
            self._on_track(rec, p)
        elif stream == "v2x.cam":
            self._on_cam(rec, p)
        elif stream == "sensing.gnss":
            self._gnss[p.get("node_id", rec.entity)] = (self.t, float(p.get("x", 0.0)),
                                                        float(p.get("y", 0.0)))
        elif stream == "link.metrics":
            self._on_link(rec, p)
        elif stream == "control.command":
            self._on_control(rec, p)

    # ---- 规则 -----------------------------------------------------------
    def _on_objects(self, rec, p) -> None:
        sid = p.get("sensor_id", rec.source)
        objs = p.get("objects", [])
        self._last_objs[sid] = objs
        for o in objs:
            cls = o.get("cls")
            if cls not in ("stop_sign", "obstacle"):
                continue
            key = (sid, int(o.get("object_id", 0)))
            near_map = any(math.hypot(o["x"] - mx, o["y"] - my) < 3.0
                           for mx, my in self.static_map)
            if near_map:
                continue
            confirmed = False
            for other, oobjs in self._last_objs.items():
                if other == sid:
                    continue
                for oo in oobjs:
                    if math.hypot(oo["x"] - o["x"], oo["y"] - o["y"]) < 2.5:
                        confirmed = True
                        break
                if confirmed:
                    break
            self._obj_streak[key] = self._obj_streak.get(key, 0) + 1 if not confirmed else 0
            if not confirmed and self._obj_streak[key] >= 2:
                kind = next((s.kind for s in self.scene.sensors if s.id == sid), "rsu")
                self._alert("ghost_object", f"{kind}:{sid}",
                            {"x": o["x"], "y": o["y"], "cls": cls}, rec.t_wall_ns)

    def _on_track(self, rec, p) -> None:
        sid = p.get("sensor_id", rec.source)
        objs = self._last_objs.get(sid, [])
        if not objs:
            return
        ox, oy = p.get("pos_x"), p.get("pos_y")
        if ox is None:
            return
        sensor = next((s for s in self.scene.sensors if s.id == sid), None)
        if sensor is None:
            return
        true_range = math.hypot(ox - sensor.x, oy - sensor.y)
        key = (sid, int(p.get("target_id", 0)))
        resid = float(p.get("range_m", true_range)) - true_range
        hist = self._resid[key]
        hist.append(resid)
        if len(hist) >= 5:
            vals = sorted(hist)[1:-1]  # 去掉极值，抑制单帧野值
            mean = sum(vals) / len(vals)
            if mean > 1.5:
                self._bias_streak[key] = self._bias_streak.get(key, 0) + 1
                if self._bias_streak[key] >= 3:
                    self._alert("radar_range_bias", rec.entity,
                                {"mean_residual_m": round(mean, 2),
                                 "range_m": p.get("range_m"),
                                 "true_range": round(true_range, 2)},
                                rec.t_wall_ns)
            else:
                self._bias_streak[key] = 0

    def _on_cam(self, rec, p) -> None:
        pid = p.get("sender_pseudo", rec.entity)
        pos = (p.get("posx", 0.0), p.get("posy", 0.0))
        self._last_cam[pid] = {"pos": pos, "t": self.t}
        hist = self._cam_track[pid]
        if hist and self.t - hist[-1][0] < 1.0:
            d = math.hypot(pos[0] - hist[-1][1], pos[1] - hist[-1][2])
            if d > 25.0:
                self._alert("cam_position_jump", f"veh:{pid}",
                            {"jump_m": round(d, 1)}, rec.t_wall_ns)
        hist.append((self.t, pos[0], pos[1]))
        g = self._gnss.get(pid)
        if g and self.t - g[0] < 0.5:
            dg = math.hypot(pos[0] - g[1], pos[1] - g[2])
            if dg > 12.0:
                self._alert("cam_gnss_mismatch", f"veh:{pid}",
                            {"cam_gnss_m": round(dg, 1)}, rec.t_wall_ns)

    def _on_link(self, rec, p) -> None:
        cbr = float(p.get("cbr", 0.0))
        self._cbr_hist.append(cbr)
        if len(self._cbr_hist) >= 10:
            rise = cbr - self._cbr_hist[0]
            if cbr > 0.7 and rise > 0.2:
                self._alert("cbr_spike", "net:pc5",
                            {"cbr": round(cbr, 3), "rise": round(rise, 3)}, rec.t_wall_ns)
        if float(p.get("per", 0.0)) > 0.5:
            self._alert("link_degraded", "net:pc5",
                        {"per": p.get("per"), "jam": p.get("jam")}, rec.t_wall_ns)

    def _on_control(self, rec, p) -> None:
        ttc = float(p.get("params", {}).get("ttc_s", 0.0))
        if ttc >= 10.0:
            self._alert("brake_without_hazard", f"veh:{p.get('dst', '')}",
                        {"ttc": ttc, "src": p.get("src")}, rec.t_wall_ns)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        by_rule: dict[str, int] = defaultdict(int)
        for a in self.alerts:
            by_rule[a.rule] += 1
        lat = sorted(a.latency_ms for a in self.alerts if a.latency_ms > 0)
        def q(p: float) -> float:
            if not lat:
                return 0.0
            return round(lat[min(len(lat) - 1, int(p * (len(lat) - 1)))], 3)
        return {
            "total": len(self.alerts),
            "by_rule": dict(by_rule),
            "latency_ms": {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "n": len(lat)},
            "recent": [a.to_dict() for a in self.alerts[-30:]],
        }

    def dump(self, path) -> None:
        import json
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for a in self.alerts:
                fh.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
