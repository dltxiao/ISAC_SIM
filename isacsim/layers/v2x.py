"""V2X 层：CAM/CPM/DENM/SPaT 报文、空口负载与 DCC 拥塞控制。

信道模型为参数化模型（非协议级仿真）：
- CBR = 归一化 Offered Load（含攻击注入的额外负载）+ 干扰
- PER 随 CBR 非线性上升；时延含排队项
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ..config import SceneCfg
from ..core.rng import rng_for
from .common import is_equipped

CAPACITY_MSGS_PER_S = 1200.0     # 10 MHz PC5 的粗略等效容量（200–300 B 报文）
BASE_MSG_BYTES = 240


class V2XLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.rng = rng_for(scene.seed, "v2x:channel")
        self.cbr = 0.05
        self.dcc_factor = 1.0
        self.offered = 0.0
        self.per = 0.01
        self.delay_ms = 2.0
        self._msg_id: dict[str, int] = defaultdict(int)
        self._last_emit: dict[str, int] = {}
        self._denm_sent: dict[str, float] = {}
        self._tls_state: str = ""

    # ------------------------------------------------------------------
    def _channel(self, ctx, equipped_speed: float, extra_load: float,
                 jam: float, delay_bonus: float) -> None:
        vehicles = ctx.vehicles
        n_eq = sum(1 for v in vehicles.values() if is_equipped(v.vid, self.scene.vehicles.equipped_ratio))
        rate = 10.0 * self.dcc_factor
        cam_load = n_eq * rate
        rsu_load = len([s for s in self.scene.sensors if s.kind == "rsu"]) * 10.0
        self.offered = cam_load + rsu_load + extra_load
        self.cbr = min(0.98, 0.92 * self.offered / CAPACITY_MSGS_PER_S + jam)
        # DCC：高 CBR 降频，带迟滞，避免振荡
        if self.cbr > 0.65 and self.dcc_factor > 0.5:
            self.dcc_factor = max(0.5, self.dcc_factor - 0.1)
        elif self.cbr < 0.55 and self.dcc_factor < 1.0:
            self.dcc_factor = min(1.0, self.dcc_factor + 0.05)
        base_per = 0.01 + 0.85 * max(0.0, self.cbr - 0.55) / 0.45
        self.per = min(0.95, base_per + jam * 0.6)
        self.delay_ms = 2.0 + 45.0 * self.cbr ** 3 + delay_bonus

    # ------------------------------------------------------------------
    def _outbox(self, ctx, stream: str, payload: dict[str, Any] | None) -> None:
        """把平台需要消费的消息放入本步 outbox；平台关闭时不累积。"""
        if payload is None:
            return
        if ctx.extras.get("layers", {}).get("platform") is None:
            return
        ctx.extras.setdefault("v2x_outbox", []).append({
            "stream": stream, "payload": payload, "t": ctx.t,
        })

    def tick(self, ctx) -> None:
        t = ctx.t
        step = ctx.step_s
        vehicles = ctx.vehicles
        extras = ctx.extras

        extra_load = float(extras.get("v2x_extra_load", 0.0))
        jam = float(extras.get("link_jam", 0.0))
        delay_bonus = float(extras.get("link_delay_ms", 0.0))
        self._channel(ctx, 0.0, extra_load, jam, delay_bonus)

        # 1 Hz SPaT
        if int(t) != int(t - step):
            for sensor in self.scene.sensors:
                if sensor.kind != "rsu":
                    continue
                state = ""
                if ctx.sumo is not None and self.scene.map.get("signals"):
                    state = ctx.sumo.signal_state(self.scene.map["signals"][0])
                payload = ctx.emit("v2x.spat", sensor.id, f"rsu:{sensor.id}", {
                    "msg_type": "spat",
                    "intersection": (self.scene.map.get("signals") or ["C1"])[0],
                    "signal_state": state,
                    "cbr": round(self.cbr, 3),
                })
                self._outbox(ctx, "v2x.spat", payload)

        # CAM：10 Hz（DCC 降频；紧急事件升频到 20 Hz）
        cam_period = 1.0 / max(1.0, 10.0 * self.dcc_factor)
        emergency = float(extras.get("emergency_until", 0.0)) > t
        if emergency:
            cam_period = 0.05
        for v in vehicles.values():
            if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                continue
            last = self._last_emit.get(v.vid, -1e9)
            if t - last + 1e-9 < cam_period:
                continue
            self._last_emit[v.vid] = t
            self._msg_id[v.vid] += 1
            lost = bool(self.rng.random() < self.per)
            delay = max(0.5, self.delay_ms + float(self.rng.normal(0.0, 0.6)))
            ctx.emit("v2x.cam", v.vid, f"veh:{v.vid}", {
                "sender_pseudo": v.vid,
                "pseudonym": f"PN-{v.vid}",
                "msg_id": self._msg_id[v.vid],
                "msg_type": "cam",
                "posx": round(v.x, 3),
                "posy": round(v.y, 3),
                "spdx": round(v.vx, 3),
                "spdy": round(v.vy, 3),
                "heading_deg": round(v.heading_deg, 1),
                "rate_hz": round(1.0 / cam_period, 1),
                "cert_status": "valid",
                "delay_ms": round(delay, 2),
                "lost": lost,
                "cbr": round(self.cbr, 3),
            })

        # CPM：RSU 10 Hz 广播目标列表摘要
        if int(round(t * 10)) != int(round((t - step) * 10)):
            for sensor in self.scene.sensors:
                if sensor.kind != "rsu":
                    continue
                objs = ctx.extras.get(f"objects:{sensor.id}") or []
                digest = [
                    {"object_id": o.get("object_id"), "cls": o.get("cls"),
                     "x": o.get("x"), "y": o.get("y")}
                    for o in objs[:12]
                ]
                lost = bool(self.rng.random() < self.per)
                payload = ctx.emit("v2x.cpm", sensor.id, f"rsu:{sensor.id}", {
                    "msg_type": "cpm",
                    "sender": sensor.id,
                    "object_count": len(objs),
                    "objects": digest,
                    "delay_ms": round(self.delay_ms, 2),
                    "lost": lost,
                    "cbr": round(self.cbr, 3),
                })
                self._outbox(ctx, "v2x.cpm", payload)

        # DENM：由控制层写入 brake_events
        events = extras.pop("brake_events", [])
        for ev in events:
            vid = ev.get("vid")
            if not vid:
                continue
            last = self._denm_sent.get(vid, -1e9)
            if t - last < 2.0:
                continue
            self._denm_sent[vid] = t
            lost = bool(self.rng.random() < self.per)
            payload = ctx.emit("v2x.denm", vid, f"veh:{vid}", {
                "sender_pseudo": vid,
                "msg_type": "denm",
                "cause": ev.get("cause", "emergency_brake"),
                "posx": round(ev.get("x", 0.0), 3),
                "posy": round(ev.get("y", 0.0), 3),
                "delay_ms": round(self.delay_ms, 2),
                "lost": lost,
                "cbr": round(self.cbr, 3),
            })
            self._outbox(ctx, "v2x.denm", payload)

        # 链路指标 10 Hz
        if int(round(t * 10)) != int(round((t - step) * 10)):
            ctx.emit("link.metrics", "MEC", "net:pc5", {
                "cbr": round(self.cbr, 3),
                "offered_msgs_s": round(self.offered, 1),
                "per": round(self.per, 4),
                "delay_p50_ms": round(self.delay_ms, 2),
                "dcc_factor": round(self.dcc_factor, 2),
                "jam": round(jam, 3),
                "capacity_msgs_s": CAPACITY_MSGS_PER_S,
            })
