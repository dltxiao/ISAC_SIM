"""四类官方跨层攻击剧本。

每个剧本都是多阶段、跨层的，并且只作用于"发射器输出侧"：
- 感知篡改 → 进入目标列表（A1/A2）或雷达回波（A3）
- 控制指令注入 → 直接写到控制面（A2/A3）
- 链路干扰 → 提高 PER（A2）
- 身份/指纹克隆 → 认证与 V2X 面（A4）
"""
from __future__ import annotations

import math
from typing import Any

from ..config import SceneCfg
from ..truth.model import TruthEvent
from ..layers.common import is_equipped
from .base import (AttackInstance, AttackSpec, ahead, ghost_object, pick_rsu,
                   pick_vehicle)


def _level(amp: float, lo: float, hi: float) -> float:
    return lo + (hi - lo) * max(0.0, min(1.0, amp))


class CampaignA1(AttackInstance):
    """感知数据篡改诱导通信拥塞。"""

    atype = "A1"
    label = "感知篡改→通信拥塞"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.ghosts: list[dict[str, Any]] = []
        self.rsu_id = ""
        self.veh_id = ""
        self.extra_load = 0.0

    def start(self, ctx) -> None:
        super().start(ctx)
        rsu = pick_rsu(self.scene, ctx, self.seed)
        veh = pick_vehicle(ctx, self.seed, "a1")
        if veh is None or rsu is None:
            self.finished = True
            return
        n = 1 + int(round(self.spec.amplitude * 4))
        self.ghosts = [
            ghost_object(veh, 9000 + i, dist=22.0 + 9.0 * i,
                         cls="car" if i % 2 == 0 else "obstacle",
                         lateral=0.4 * (i % 3) - 0.4)
            for i in range(n)
        ]
        self.rsu_id, self.veh_id = rsu.id, veh.vid
        self.extra_load = _level(self.spec.amplitude, 150.0, 650.0)

        def hook(payload, _ctx):
            if payload.get("sensor_id") != self.rsu_id:
                return payload
            payload["objects"] = list(payload.get("objects", [])) + [
                dict(g) for g in self.ghosts
            ]
            return payload

        self.hook(ctx, "sensing.objects", hook)
        ctx.stats.note("attack", f"A1 注入 {n} 个幽灵目标（{self.rsu_id} → {self.veh_id}）")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        ctx.extras["v2x_extra_load"] = ctx.extras.get("v2x_extra_load", 0.0) + self.extra_load
        # 攻击者伪造 DENM 洪泛
        if int(round(ctx.t * 2)) != int(round((ctx.t - ctx.step_s) * 2)):
            veh = ctx.vehicles.get(self.veh_id)
            x = veh.x if veh else 0.0
            y = veh.y if veh else 0.0
            ctx.emit("v2x.denm", "ATK-A1", "attacker:A1", {
                "sender_pseudo": "ATK-A1",
                "msg_type": "denm",
                "cause": "obstacle_warning",
                "posx": round(x + 30.0, 2),
                "posy": round(y, 2),
                "delay_ms": 3.0,
                "lost": False,
                "cbr": 0.0,
                "forged": True,
            })

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"rsu:{self.rsu_id}", f"veh:{self.veh_id}", "attacker:A1"]
        ev.domains = ["perception", "v2x", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"ghost_count": len(self.ghosts), "extra_load": self.extra_load,
                     "rsu": self.rsu_id, "victim": self.veh_id}
        ev.envelope = {"in_envelope": self.extra_load >= 250.0,
                       "criterion": "extra_load >= 250 msg/s"}
        ev.expected_evidence = ["sensing.objects", "v2x.denm", "link.metrics", "control.command"]
        ev.stages = [
            {"name": "伪造路侧目标", "t0": round(self.t0, 2), "t1": round(self.t0 + 2.0, 2)},
            {"name": "通信负载上升", "t0": round(self.t0 + 2.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA2(AttackInstance):
    """物理环境欺骗 + 控制指令误导。"""

    atype = "A2"
    label = "假停止牌+控制误导"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.sign: dict[str, Any] | None = None
        self.rsu_id = ""
        self.veh_id = ""
        self.jam = 0.0
        self._brake_done = False

    def start(self, ctx) -> None:
        super().start(ctx)
        rsu = pick_rsu(self.scene, ctx, self.seed + 1)
        veh = pick_vehicle(ctx, self.seed, "a2") or pick_vehicle(ctx, self.seed + 1, "a2")
        if veh is None or rsu is None:
            self.finished = True
            return
        self.sign = ghost_object(veh, 9100, dist=28.0, cls="stop_sign")
        self.rsu_id, self.veh_id = rsu.id, veh.vid
        self.jam = _level(self.spec.amplitude, 0.05, 0.4)

        def hook(payload, _ctx):
            if payload.get("sensor_id") != self.rsu_id:
                return payload
            payload["objects"] = list(payload.get("objects", [])) + [dict(self.sign)]
            return payload

        self.hook(ctx, "sensing.objects", hook)
        ctx.stats.note("attack", f"A2 伪造停止牌（{self.rsu_id}），并干扰链路")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        ctx.extras["link_jam"] = ctx.extras.get("link_jam", 0.0) + self.jam
        if not self._brake_done and ctx.t >= self.t0 + 1.0:
            self._brake_done = True
            control = ctx.extras.get("layers", {}).get("control")
            if control is not None and self.veh_id in ctx.vehicles:
                control.inject_brake(ctx, self.veh_id,
                                     decel=_level(self.spec.amplitude, 3.0, 5.5),
                                     duration=1.8)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"rsu:{self.rsu_id}", f"veh:{self.veh_id}", "attacker:A2"]
        ev.domains = ["perception", "control", "link"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"sign_at": self.sign and self.sign["x"], "jam": self.jam,
                     "victim": self.veh_id}
        ev.envelope = {"in_envelope": self.jam >= 0.05, "criterion": "jam >= 0.05"}
        ev.expected_evidence = ["sensing.objects", "control.command", "link.metrics", "v2x.denm"]
        ev.stages = [
            {"name": "伪造停止牌", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "注入制动指令", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t0 + 3.0, 2)},
            {"name": "链路干扰", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA3(AttackInstance):
    """跨层隐蔽攻击：雷达回波参数渐进篡改 + 延迟控制指令。"""

    atype = "A3"
    label = "雷达回波渐偏（隐蔽）"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.bias_max = 0.0
        self._cmd_done = False
        self._modified = 0

    def start(self, ctx) -> None:
        super().start(ctx)
        veh = pick_vehicle(ctx, self.seed, "a3")
        if veh is None:
            self.finished = True
            return
        self.veh_id = veh.vid
        self.bias_max = _level(self.spec.amplitude, 0.8, 3.0)
        entity = f"veh:{self.veh_id}"

        def hook(payload, _ctx):
            if payload.get("entity") != entity:
                return payload
            frac = max(0.0, min(1.0, (_ctx.t - self.t0) / max(self.spec.duration, 1e-6)))
            payload["range_m"] = round(float(payload["range_m"]) + self.bias_max * frac, 3)
            self._modified += 1
            return payload

        self.hook(ctx, "sensing.radar.echo", hook)
        ctx.stats.note("attack", f"A3 对 {self.veh_id} 回波距离渐偏，最大 {self.bias_max:.1f} m")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        if not self._cmd_done and ctx.t >= self.t0 + self.spec.duration * 0.6:
            self._cmd_done = True
            control = ctx.extras.get("layers", {}).get("control")
            if control is not None and self.veh_id in ctx.vehicles:
                control.inject_brake(ctx, self.veh_id, decel=1.2, duration=0.8)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"veh:{self.veh_id}", f"gnb:{self.scene.sensors[-1].id}"]
        ev.domains = ["radar", "control", "perception"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"bias_max_m": self.bias_max, "victim": self.veh_id,
                     "ramp_s": self.spec.duration}
        ev.envelope = {"in_envelope": self.bias_max >= 1.0,
                       "criterion": "bias >= 1.0 m"}
        ev.expected_evidence = ["sensing.radar.echo", "sensing.radar.track", "sensing.objects"]
        ev.stages = [
            {"name": "回波距离渐偏", "t0": round(self.t0, 2), "t1": round(self.t1, 2)},
            {"name": "延迟注入小指令", "t0": round(self.t0 + self.spec.duration * 0.6, 2),
             "t1": round(self.t0 + self.spec.duration * 0.6 + 1.0, 2)},
        ]
        return ev


class CampaignA4(AttackInstance):
    """双因素仿冒：克隆通信标识 + 复制信道指纹。"""

    atype = "A4"
    label = "双因素仿冒"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.victim = ""
        self.spoof_rate = 2.0
        self._last = -1.0
        self._fp: dict[str, float] = {}
        self._count = 0

    def start(self, ctx) -> None:
        super().start(ctx)
        equipped = [v for v in sorted(ctx.vehicles)
                    if is_equipped(v, self.scene.vehicles.equipped_ratio)]
        if not equipped:
            self.finished = True
            return
        from .base import stable_pick
        self.victim = stable_pick(equipped, self.seed, "a4")
        veh = ctx.vehicles[self.victim]
        from ..layers.auth import baseline_fingerprint

        self._fp = baseline_fingerprint(self.victim)
        self.spoof_rate = _level(self.spec.amplitude, 2.0, 20.0)
        ctx.emit("auth.cert", "ATK-A4", f"attacker:{self.victim}", {
            "node_id": self.victim,
            "event": "clone",
            "cert_id": f"CERT-{self.victim}-clone",
            "pseudonym": f"PN-{self.victim}",
            "status": "valid",
            "cloned": True,
        })
        ctx.stats.note("attack", f"A4 克隆 {self.victim} 的标识与指纹")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        period = 1.0 / max(0.5, self.spoof_rate)
        if ctx.t - self._last < period:
            return
        self._last = ctx.t
        self._count += 1
        victim = ctx.vehicles.get(self.victim)
        if victim is None:
            return
        # 与真实位置偏离的伪造 CAM（同一伪标识）
        x = victim.x + 45.0
        y = victim.y + 8.0
        ctx.emit("v2x.cam", f"ATK-{self.victim}", f"veh:{self.victim}", {
            "sender_pseudo": self.victim,
            "pseudonym": f"PN-{self.victim}",
            "msg_id": 900000 + self._count,
            "msg_type": "cam",
            "posx": round(x, 3),
            "posy": round(y, 3),
            "spdx": 12.0,
            "spdy": 0.0,
            "heading_deg": 0.0,
            "rate_hz": 10.0,
            "cert_status": "valid",
            "delay_ms": 3.0,
            "lost": False,
            "cbr": 0.0,
            "cloned": True,
        })
        ctx.emit("auth.fingerprint", f"ATK-{self.victim}", f"veh:{self.victim}", {
            "node_id": self.victim,
            "cfo_ppm": self._fp["cfo_ppm"],
            "iq_gain_db": self._fp["iq_gain_db"],
            "iq_phase_deg": self._fp["iq_phase_deg"],
            "rssi_dbm": round(self._fp["rssi_base_dbm"] + 4.0, 1),
            "auth_result": "pass",
            "cloned": True,
        })

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"veh:{self.victim}", "attacker:A4"]
        ev.domains = ["auth", "v2x", "uu"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"victim": self.victim, "spoof_rate": self.spoof_rate,
                     "fingerprint": self._fp}
        ev.envelope = {"in_envelope": self.spoof_rate >= 2.0,
                       "criterion": "spoof_rate >= 2 Hz"}
        ev.expected_evidence = ["auth.cert", "auth.fingerprint", "v2x.cam"]
        ev.stages = [
            {"name": "克隆标识", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "复制指纹并注入消息", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


CAMPAIGNS = {"A1": CampaignA1, "A2": CampaignA2, "A3": CampaignA3, "A4": CampaignA4}
