"""单实体/单层攻击原语：可由 Web 直接下发，也可作为剧本组件。"""
from __future__ import annotations

from typing import Any

from ..config import SceneCfg
from ..truth.model import TruthEvent
from .base import (AttackInstance, AttackSpec, ahead, ghost_object, pick_rsu,
                   pick_vehicle)

LABELS = {
    "ghost_object": "伪造感知目标",
    "range_bias": "雷达距离偏置",
    "echo_suppress": "雷达回波删除",
    "denm_flood": "DENM 洪泛",
    "jam": "链路干扰",
    "id_clone": "标识克隆",
    "brake_inject": "制动指令注入",
    "gnss_spoof": "GNSS 欺骗",
}

DOMAINS = {
    "ghost_object": ["perception", "control"],
    "range_bias": ["radar", "perception"],
    "echo_suppress": ["radar"],
    "denm_flood": ["v2x", "link"],
    "jam": ["link"],
    "id_clone": ["auth", "v2x"],
    "brake_inject": ["control"],
    "gnss_spoof": ["gnss", "perception"],
}


class PrimitiveAttack(AttackInstance):
    is_campaign = False

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.kind = spec.type
        if self.kind not in LABELS:
            raise ValueError(f"未知攻击原语：{self.kind}")
        self.label = LABELS[self.kind]
        self.veh_id = ""
        self.rsu_id = ""
        self._last = -1.0
        self._brake_done = False
        self._bias = 0.0

    def start(self, ctx) -> None:
        super().start(ctx)
        veh = pick_vehicle(ctx, self.seed, self.kind)
        rsu = pick_rsu(self.scene, ctx, self.seed)
        if veh is None:
            self.finished = True
            return
        self.veh_id = veh.vid
        self.rsu_id = rsu.id if rsu else ""
        kind = self.kind
        amp = self.spec.amplitude

        if kind == "ghost_object":
            ghost = ghost_object(veh, 9200, dist=26.0, cls="obstacle")
            target_rsu = self.rsu_id

            def hook(payload, _ctx):
                if payload.get("sensor_id") != target_rsu:
                    return payload
                payload["objects"] = list(payload.get("objects", [])) + [dict(ghost)]
                return payload

            self.hook(ctx, "sensing.objects", hook)
        elif kind == "range_bias":
            self._bias = 1.0 + 3.0 * amp
            entity = f"veh:{self.veh_id}"

            def hook(payload, _ctx):
                if payload.get("entity") != entity:
                    return payload
                payload["range_m"] = round(float(payload["range_m"]) + self._bias, 3)
                return payload

            self.hook(ctx, "sensing.radar.echo", hook)
        elif kind == "echo_suppress":
            entity = f"veh:{self.veh_id}"

            def hook(payload, _ctx):
                return None if payload.get("entity") == entity else payload

            self.hook(ctx, "sensing.radar.echo", hook)
        elif kind == "gnss_spoof":
            entity = f"veh:{self.veh_id}"
            dx, dy = 20.0 + 40.0 * amp, 10.0

            def hook(payload, _ctx):
                if payload.get("node_id") != self.veh_id:
                    return payload
                payload["x"] = round(float(payload["x"]) + dx, 3)
                payload["y"] = round(float(payload["y"]) + dy, 3)
                return payload

            self.hook(ctx, "sensing.gnss", hook)
        elif kind == "brake_inject":
            pass  # tick 中执行
        elif kind == "jam":
            pass
        elif kind == "denm_flood":
            pass
        elif kind == "id_clone":
            ctx.emit("auth.cert", "ATK", f"attacker:{self.veh_id}", {
                "node_id": self.veh_id, "event": "clone",
                "cert_id": f"CERT-{self.veh_id}-clone",
                "pseudonym": f"PN-{self.veh_id}", "status": "valid", "cloned": True,
            })
        ctx.stats.note("attack", f"{kind} → {self.veh_id}")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        kind = self.kind
        amp = self.spec.amplitude
        if kind == "brake_inject":
            if not self._brake_done and ctx.t >= self.t0 + 0.5:
                self._brake_done = True
                control = ctx.extras.get("layers", {}).get("control")
                if control is not None and self.veh_id in ctx.vehicles:
                    control.inject_brake(ctx, self.veh_id, decel=4.0 + 2.0 * amp, duration=1.5)
        elif kind == "jam":
            ctx.extras["link_jam"] = ctx.extras.get("link_jam", 0.0) + 0.1 + 0.4 * amp
        elif kind == "denm_flood":
            ctx.extras["v2x_extra_load"] = ctx.extras.get("v2x_extra_load", 0.0) + 300 + 1200 * amp
            if ctx.t - self._last >= 0.2:
                self._last = ctx.t
                veh = ctx.vehicles.get(self.veh_id)
                if veh is not None:
                    ctx.emit("v2x.denm", "ATK", "attacker:denm", {
                        "sender_pseudo": "ATK",
                        "msg_type": "denm",
                        "cause": "forged_hazard",
                        "posx": round(veh.x + 20.0, 2),
                        "posy": round(veh.y, 2),
                        "delay_ms": 3.0, "lost": False, "cbr": 0.0, "forged": True,
                    })
        elif kind == "id_clone":
            if ctx.t - self._last >= 0.2:
                self._last = ctx.t
                veh = ctx.vehicles.get(self.veh_id)
                if veh is not None:
                    ctx.emit("v2x.cam", f"ATK-{self.veh_id}", f"veh:{self.veh_id}", {
                        "sender_pseudo": self.veh_id,
                        "pseudonym": f"PN-{self.veh_id}",
                        "msg_id": 800000 + int(ctx.t * 10),
                        "msg_type": "cam",
                        "posx": round(veh.x + 40.0, 3),
                        "posy": round(veh.y + 6.0, 3),
                        "spdx": veh.vx, "spdy": veh.vy,
                        "heading_deg": round(veh.heading_deg, 1),
                        "rate_hz": 10.0, "cert_status": "valid",
                        "delay_ms": 3.0, "lost": False, "cbr": 0.0, "cloned": True,
                    })

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"veh:{self.veh_id}", f"rsu:{self.rsu_id}"] if self.rsu_id else [f"veh:{self.veh_id}"]
        ev.domains = DOMAINS.get(self.kind, ["misc"])
        ev.amplitude = self.spec.amplitude
        ev.params = {"kind": self.kind, "victim": self.veh_id}
        ev.envelope = {"in_envelope": True, "criterion": "primitive"}
        ev.expected_evidence = [self.kind]
        return ev
