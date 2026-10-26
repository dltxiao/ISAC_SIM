"""感知层：雷达回波参数、点迹、路侧目标列表、帧时钟、GNSS。

所有量均由 SUMO 真值几何反演，不进行电磁仿真；噪声、检测概率、
遮挡、时钟偏差可配。攻击只作用于本层输出（回波/点迹/目标列表）。
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ..config import SceneCfg, SensorCfg
from ..core.rng import rng_for
from ..world.geometry import (bearing_deg, distance, in_fov, radial_velocity)

CLS_OF_TYPE = {"truck": "truck", "bus": "truck", "trailer": "truck",
               "motorcycle": "motorcycle", "pedestrian": "pedestrian",
               "bicycle": "bicycle"}


def vehicle_class(type_id: str) -> str:
    return CLS_OF_TYPE.get(type_id, "car")


class SensingLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.rngs = {s.id: rng_for(scene.seed, f"sensing:{s.id}") for s in scene.sensors}
        self.last_seq = {s.id: -1 for s in scene.sensors}
        self._track_ids: dict[tuple[str, str], int] = {}
        self._next_tid: dict[str, int] = defaultdict(int)
        self._track_age: dict[tuple[str, int], int] = {}
        self.last_objects: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.last_frame_t: dict[str, float] = {}

    # ------------------------------------------------------------------
    def _tid(self, sid: str, vid: str) -> int:
        key = (sid, vid)
        if key not in self._track_ids:
            self._next_tid[sid] += 1
            self._track_ids[key] = self._next_tid[sid]
        tid = self._track_ids[key]
        self._track_age[(sid, tid)] = self._track_age.get((sid, tid), 0) + 1
        return tid

    def clock(self, s: SensorCfg, t: float, rng) -> float:
        jitter = float(rng.normal(0.0, 0.4)) / 1000.0
        return t + s.offset_ms / 1000.0 + s.drift_ppm * 1e-6 * t + jitter

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        t = ctx.t
        vehicles = ctx.vehicles
        occluders = [
            (v.vid, v.x, v.y, v.length, v.width, v.heading_deg)
            for v in vehicles.values() if v.length >= 6.0
        ]
        for sensor in self.scene.sensors:
            seq = int(round(t * sensor.rate_hz))
            if seq == self.last_seq[sensor.id]:
                continue
            self.last_seq[sensor.id] = seq
            self._emit_frame(ctx, sensor, seq, vehicles, occluders)
        # GNSS 10 Hz
        if int(round(t * 10)) != int(round((t - ctx.step_s) * 10)):
            self._emit_gnss(ctx, vehicles)

    # ------------------------------------------------------------------
    def _visible(self, sensor: SensorCfg, vehicles, occluders, rng) -> list[tuple[Any, float]]:
        out: list[tuple[Any, float]] = []
        for v in vehicles.values():
            dx, dy = v.x - sensor.x, v.y - sensor.y
            r = math.hypot(dx, dy)
            if r > sensor.range_m or r < 0.5:
                continue
            b = bearing_deg(dx, dy)
            if not in_fov(b, sensor.yaw_deg, sensor.fov_deg):
                continue
            blocked = False
            for (ovid, ox, oy, ol, ow, oh) in occluders:
                if ovid == v.vid:
                    continue
                from ..world.geometry import point_segment_distance
                dist, tt = point_segment_distance(ox, oy, sensor.x, sensor.y, v.x, v.y)
                if 0.05 < tt < 0.95 and dist < max(ol, ow) / 2.0 + 0.6:
                    blocked = True
                    break
            if blocked:
                continue
            snr = 32.0 - 0.045 * r + (3.0 if v.cls == "truck" else 0.0)
            p_det = 1.0 / (1.0 + math.exp(-(snr - 4.0) / 2.0))
            if rng.random() > p_det:
                continue
            out.append((v, r))
        return out

    def _static_visible(self, sensor: SensorCfg) -> list[dict[str, Any]]:
        out = []
        for obj in self.scene.static_objects:
            dx, dy = obj["x"] - sensor.x, obj["y"] - sensor.y
            r = math.hypot(dx, dy)
            if r > sensor.range_m:
                continue
            if not in_fov(bearing_deg(dx, dy), sensor.yaw_deg, sensor.fov_deg):
                continue
            out.append(obj)
        return out

    # ------------------------------------------------------------------
    def _emit_frame(self, ctx, sensor: SensorCfg, seq: int, vehicles, occluders) -> None:
        import math as _m

        rng = self.rngs[sensor.id]
        t = ctx.t
        ts = self.clock(sensor, t, rng)
        objects: list[dict[str, Any]] = []
        targets = self._visible(sensor, vehicles, occluders, rng)

        for veh, true_range in targets:
            tid = self._tid(sensor.id, veh.vid)
            dx, dy = veh.x - sensor.x, veh.y - sensor.y
            az = bearing_deg(dx, dy) - sensor.yaw_deg
            az = (az + 180.0) % 360.0 - 180.0
            el = _m.degrees(_m.atan2(1.0 - sensor.z, max(_m.hypot(dx, dy), 1e-3)))
            rng_noise = 0.12 + 0.003 * true_range
            r_meas = true_range + sensor.bias_m + float(rng.normal(0.0, rng_noise))
            rv = radial_velocity(veh.vx, veh.vy, dx, dy) + float(rng.normal(0.0, 0.15))
            snr = 32.0 - 0.045 * true_range
            rcs = 12.0 if veh.cls == "truck" else 9.0
            echo = {
                "sensor_id": sensor.id,
                "entity": f"veh:{veh.vid}",
                "frame_seq": seq,
                "target_id": tid,
                "range_m": round(r_meas, 3),
                "radial_vel_mps": round(rv, 3),
                "azimuth_deg": round(az, 3),
                "elevation_deg": round(el, 3),
                "rcs_dbsm": round(rcs + float(rng.normal(0.0, 0.6)), 2),
                "snr_db": round(snr + float(rng.normal(0.0, 0.8)), 2),
                "amplitude": round(10 ** (snr / 20.0), 3),
                "phase_rad": round(float(rng.uniform(0, 2 * _m.pi)), 4),
                "ts_offset_ms": round((ts - t) * 1000.0, 3),
            }
            final_echo = ctx.emit("sensing.radar.echo", sensor.id, f"veh:{veh.vid}",
                                  dict(echo), t=t)
            base = final_echo if final_echo is not None else echo

            track = {
                "sensor_id": sensor.id,
                "entity": f"veh:{veh.vid}",
                "frame_seq": seq,
                "target_id": tid,
                "range_m": round(base["range_m"] + float(rng.normal(0.0, 0.05)), 3),
                "radial_vel_mps": round(base["radial_vel_mps"] + float(rng.normal(0.0, 0.03)), 3),
                "azimuth_deg": round(base["azimuth_deg"], 3),
                "elevation_deg": base["elevation_deg"],
                "rcs_dbsm": base["rcs_dbsm"],
                "snr_db": base["snr_db"],
                "confidence": round(min(0.99, 0.75 + snr / 200.0), 3),
                "pos_x": round(veh.x + float(rng.normal(0.0, 0.15)), 3),
                "pos_y": round(veh.y + float(rng.normal(0.0, 0.15)), 3),
            }
            ctx.emit("sensing.radar.track", sensor.id, f"veh:{veh.vid}", dict(track), t=t)

            objects.append({
                "object_id": tid,
                "cls": veh.cls,
                "x": round(veh.x + float(rng.normal(0.0, 0.15)), 3),
                "y": round(veh.y + float(rng.normal(0.0, 0.15)), 3),
                "z": 0.8,
                "l": round(veh.length, 2),
                "w": round(veh.width, 2),
                "h": 1.6 if veh.cls != "truck" else 2.8,
                "vx": round(veh.speed * math.cos(math.radians(veh.heading_deg))
                            + float(rng.normal(0.0, 0.1)), 3),
                "vy": round(veh.speed * math.sin(math.radians(veh.heading_deg))
                            + float(rng.normal(0.0, 0.1)), 3),
                "heading_deg": round(veh.heading_deg, 1),
                "confidence": round(min(0.99, 0.88 + rng.normal(0.0, 0.03)), 3),
                "tracked_age": self._track_age.get((sensor.id, tid), 1),
                "occluded": False,
                "entity": f"veh:{veh.vid}",
            })

        # 静态目标（停止牌/障碍物/行人）
        for obj in self._static_visible(sensor):
            objects.append({
                "object_id": int(obj["object_id"]),
                "cls": obj.get("cls", "obstacle"),
                "x": float(obj["x"]),
                "y": float(obj["y"]),
                "z": float(obj.get("z", 1.0)),
                "l": 0.6, "w": 0.6, "h": 1.8,
                "vx": 0.0, "vy": 0.0,
                "heading_deg": 180.0,
                "confidence": 0.86,
                "tracked_age": 99,
                "occluded": False,
                "entity": f"obj:{obj['object_id']}",
            })

        payload = {
            "sensor_id": sensor.id,
            "coverage_id": sensor.coverage_id,
            "frame_seq": seq,
            "clock_offset_ms": round((ts - t) * 1000.0, 3),
            "source_kind": sensor.kind,
            "objects": objects,
        }
        final = ctx.emit("sensing.objects", sensor.id, f"rsu:{sensor.id}", payload, t=t)
        if final is not None:
            self.last_objects[sensor.id] = final.get("objects", [])
            self.last_frame_t[sensor.id] = t
            ctx.extras[f"objects:{sensor.id}"] = final.get("objects", [])

        ctx.emit("sensing.frame", sensor.id, f"rsu:{sensor.id}", {
            "sensor_id": sensor.id,
            "coverage_id": sensor.coverage_id,
            "frame_seq": seq,
            "object_count": len(objects),
            "clock_offset_ms": round((ts - t) * 1000.0, 3),
            "drift_ppm": sensor.drift_ppm,
        }, t=t)

    # ------------------------------------------------------------------
    def _emit_gnss(self, ctx, vehicles) -> None:
        rng = rng_for(self.scene.seed, "gnss:global")
        for v in vehicles.values():
            sigma = 0.4
            hdop = 0.8
            # 路口附近模拟城市峡谷，定位精度下降
            if distance(v.x, v.y, 200.0, 0.0) < 40.0:
                sigma = 1.2
                hdop = 2.6
            x = v.x + float(rng.normal(0.0, sigma))
            y = v.y + float(rng.normal(0.0, sigma))
            lat = 31.2304 + y / 111_320.0
            lon = 121.4737 + x / (111_320.0 * math.cos(math.radians(31.2304)))
            ctx.emit("sensing.gnss", v.vid, f"veh:{v.vid}", {
                "node_id": v.vid,
                "x": round(x, 3),
                "y": round(y, 3),
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "alt": 8.0,
                "v_north": round(v.vy, 3),
                "v_east": round(v.vx, 3),
                "hdop": hdop,
                "fix_type": 3 if sigma < 0.6 else 2,
                "num_sats": 14 if sigma < 0.6 else 8,
            })
