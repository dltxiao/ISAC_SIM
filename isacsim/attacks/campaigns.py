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
from ..layers.common import has_adas, is_equipped, serving_sensor
from ..layers.v2x import qos_impairment
from .base import (AttackInstance, AttackSpec, Pacer, ahead, ghost_object,
                   pick_rsu, pick_vehicle, stable_pick)


def _level(amp: float, lo: float, hi: float) -> float:
    return lo + (hi - lo) * max(0.0, min(1.0, amp))


def _victim_for_rsu(ctx, rsu, scene: SceneCfg, seed: int, salt: str):
    """选一个由该 RSU 服务、且装备了 ADAS 的车辆，保证 control 域可观测。"""
    candidates = []
    for v in sorted(ctx.vehicles.values(), key=lambda x: x.vid):
        if not is_equipped(v.vid, scene.vehicles.equipped_ratio):
            continue
        if not has_adas(v.vid, scene.vehicles.adas_ratio):
            continue
        if getattr(v, "speed", 0.0) < 2.0:
            continue
        sensor = serving_sensor(v, [rsu])
        if sensor is not None and sensor.id == rsu.id:
            candidates.append(v)
    return stable_pick(candidates, seed, salt)


class CampaignA1(AttackInstance):
    """感知数据篡改诱导通信拥塞。"""

    atype = "A1"
    label = "感知篡改→通信拥塞"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.ghosts: list[dict[str, Any]] = []
        self.rsu_id = ""
        self.veh_id = ""
        self.flood_rate = 0.0
        self._amplified = False

    def start(self, ctx) -> None:
        super().start(ctx)
        rsu = pick_rsu(self.scene, ctx, self.seed)
        veh = _victim_for_rsu(ctx, rsu, self.scene, self.seed, "a1") if rsu else None
        if veh is None:
            # 备选：按 RSU 顺序找一个可服务的 ADAS 车辆
            for alt in sorted((s for s in self.scene.sensors if s.kind == "rsu"),
                              key=lambda s: s.id):
                veh = _victim_for_rsu(ctx, alt, self.scene, self.seed, "a1")
                if veh is not None:
                    rsu = alt
                    break
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
        # 真实发报速率（条/秒）；链路负载由 v2x 按实际发出的 DENM 统计
        self.flood_rate = _level(self.spec.amplitude, 120.0, 600.0)

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
        layers = ctx.extras.get("layers", {})
        v2x = layers.get("v2x")
        if v2x is None:
            return
        # 感知腿真的生效（受害车被幽灵诱导制动）后，洪泛再放大一档：
        # 这让“感知→制动→通信”的因果边成立，而不是两个互不相关的动作。
        control = layers.get("control")
        if not self._amplified and control is not None:
            if any(b.get("vid") == self.veh_id for b in getattr(control, "recent_brakes", ())):
                self._amplified = True
                ctx.stats.note("attack", f"A1 受害车已制动，DENM 洪泛放大到 {self.flood_rate * 2:.0f}/s")
        veh = ctx.vehicles.get(self.veh_id)
        x = veh.x if veh else 0.0
        y = veh.y if veh else 0.0
        rate = self.flood_rate * (2.0 if self._amplified else 1.0)
        n = int(round(rate * ctx.step_s))
        if n > 0:
            v2x.forge_denm(ctx, "ATK-A1", x + 30.0, y, n)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"rsu:{self.rsu_id}", f"veh:{self.veh_id}", "attacker:A1"]
        ev.domains = ["perception", "v2x", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"ghost_count": len(self.ghosts), "denm_rate": round(self.flood_rate, 1),
                     "rsu": self.rsu_id, "victim": self.veh_id}
        ev.envelope = {"in_envelope": self.flood_rate >= 250.0,
                       "criterion": "denm_rate >= 250 msg/s"}
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
        # 先保证“受害车由该 RSU 服务且具备 ADAS”，停止牌才会真的进入它的目标列表；
        # 这和 A1/A3 的选择口径一致，避免感知腿与受害车脱钩（审核 R14）。
        veh = _victim_for_rsu(ctx, rsu, self.scene, self.seed, "a2") if rsu else None
        if veh is None:
            for alt in sorted((s for s in self.scene.sensors if s.kind == "rsu"),
                              key=lambda x: x.id):
                veh = _victim_for_rsu(ctx, alt, self.scene, self.seed, "a2")
                if veh is not None:
                    rsu = alt
                    break
        if veh is None:
            # 兜底：保持旧行为（制动仍由 inject_brake 保证）
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
    """跨层隐蔽攻击：篡改前车雷达回波距离 → 目标列表位置偏移 → ADAS 误判；
    同时在 60% 处注入一条幅度随偏移递增的控制指令，并降低回波置信度掩盖意图。"""

    atype = "A3"
    label = "雷达回波渐偏（隐蔽）"

    TTC_TRIGGER = 2.5      # 与 ControlLayer.TTC_THRESHOLD 对齐，用于挑选“最易触发”的跟驰对
    MASK_SNR_DB = 3.0      # 回波信噪比压低的峰值（掩盖手段）

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""        # 受害车（ADAS 被误导的一方）
        self.lead_id = ""       # 被篡改回波的前车
        self.sensor_id = ""     # 受害车当前的服务传感器
        self._sensor_cfg = None
        self.bias_max = 0.0     # 计划位移（m）：真实纵向距离 - 目标表观距离
        self.lon_end = 0.0      # 攻击结束时前车的表观纵向距离（m）
        self._cmd_done = False
        self._modified = 0
        self._hooked = False
        self._retarget_t = -1e9

    # ---- 选“受害车 + 前车”对 ------------------------------------------
    def _lead_score(self, ego, sensor, objs):
        """在当前 ego 的视野里挑前车。

        返回 (tier, score, obj)：tier 0=本车道前方的跟驰对（最优），1=前方的任意目标，
        2=视野内最近的其它车（兜底，保证攻击能落地）。
        """
        hx, hy = math.cos(math.radians(ego.heading_deg)), math.sin(math.radians(ego.heading_deg))
        px, py = -hy, hx
        best = None
        for o in objs:
            ent = str(o.get("entity", ""))
            if not ent.startswith("veh:"):
                continue
            if ent == f"veh:{ego.vid}":   # 自车也在自己的传感器目标列表里，必须排除
                continue
            dx, dy = o["x"] - ego.x, o["y"] - ego.y
            lon = dx * hx + dy * hy
            lat = dx * px + dy * py
            dist = math.hypot(dx, dy)
            closing = max(ego.speed - (o.get("vx", 0.0) * hx + o.get("vy", 0.0) * hy), 0.2)
            # ADAS 只在 TTC<2.5 时刹车，而 TTC 下限受“最近可视距离 2.2 m”约束：
            # closing 必须大于约 0.9 m/s 才可能被推到阈值以下。因此评分先看可行性，
            # 再看谁接近得最快（closing 大），最后看距离。
            feasible = 0 if closing >= 1.2 else 1
            if 2.0 < lon < 60.0 and abs(lat) <= 2.2:
                cand = (0, feasible, -closing, lon, o)
            elif 2.0 < lon < 60.0 and abs(lat) <= 4.5:
                cand = (1, feasible, -closing, lon, o)
            else:
                cand = (2, 0, 0.0, dist, o)
            if best is None or cand[:4] < best[:4]:
                best = cand
        return best

    def _aim(self, ego, sensor, obj) -> None:
        """锁定前车，并算出攻击结束时要把它“摆”到多近。

        目标表观 TTC = 3.2 - 2.0×amplitude（a=0.8 → 1.6 s），已经低于 AEB 的
        2.5 s 门限；位移就是“真实纵向距离 - 表观纵向距离”。a 越大，拉得越近。
        """
        self.sensor_id = sensor.id
        self.lead_id = str(obj["entity"]).split(":", 1)[1]
        self._sensor_cfg = sensor
        hx, hy = math.cos(math.radians(ego.heading_deg)), math.sin(math.radians(ego.heading_deg))
        dx, dy = obj["x"] - ego.x, obj["y"] - ego.y
        lon = dx * hx + dy * hy
        closing = max(ego.speed - (obj.get("vx", 0.0) * hx + obj.get("vy", 0.0) * hy), 0.2)
        if lon <= 2.0:
            # 视野里没有真正的前方目标（退化为兜底）：不伪造，真值里如实记 0
            self.lon_end = round(lon, 2)
            self.bias_max = 0.0
            return
        target_ttc = 3.2 - 2.0 * max(0.0, min(1.0, self.spec.amplitude))
        if target_ttc > self.TTC_TRIGGER - 0.05:
            # 幅度太小：即使把前车拉到最近也压不到 AEB 门限以下，视为“包络外”，不改回波
            self.lon_end = round(lon, 2)
            self.bias_max = 0.0
            return
        self.lon_end = round(min(lon - 2.0, max(2.2, target_ttc * closing)), 2)
        self.bias_max = round(max(0.0, lon - self.lon_end), 2)

    def _pick_pair(self, ctx) -> bool:
        """开局挑一辆“受害车”：装备 ADAS、在跑、有服务传感器。"""
        sensing = ctx.extras.get("layers", {}).get("sensing")
        if sensing is None:
            return False
        sensors = [s for s in self.scene.sensors if s.kind in ("rsu", "gnb")]
        best = None
        for ego in sorted(ctx.vehicles.values(), key=lambda v: v.vid):
            if not (is_equipped(ego.vid, self.scene.vehicles.equipped_ratio)
                    and has_adas(ego.vid, self.scene.vehicles.adas_ratio)):
                continue
            if ego.speed < 2.0:
                continue
            sensor = serving_sensor(ego, sensors)
            if sensor is None:
                continue
            picked = self._lead_score(ego, sensor, sensing.last_objects.get(sensor.id, []))
            if picked is None:
                continue
            key = picked[:-1]
            if best is None or key < best[:len(key)]:
                best = (*key, ego, sensor, picked[-1])
        if best is None:
            return False
        ego, sensor, obj = best[-3], best[-2], best[-1]
        self.veh_id = ego.vid
        self._aim(ego, sensor, obj)
        return True

    def _repick_lead(self, ctx) -> None:
        """受害车不变，只重选前车（前车驶离/换了服务传感器时）。"""
        ego = ctx.vehicles.get(self.veh_id)
        if ego is None:
            return
        sensing = ctx.extras.get("layers", {}).get("sensing")
        sensors = [s for s in self.scene.sensors if s.kind in ("rsu", "gnb")]
        sensor = serving_sensor(ego, sensors)
        if sensor is None or sensing is None:
            return
        picked = self._lead_score(ego, sensor, sensing.last_objects.get(sensor.id, []))
        # 重选只接受“真正在前方”的目标；兜底（tier 2）会把对手车/后车当成前车，
        # 那样伪造出的回波根本不会进入受害车的 AEB 判决。
        if picked is None or picked[0] > 1:
            return
        self._aim(ego, sensor, picked[-1])

    def start(self, ctx) -> None:
        super().start(ctx)
        self.bias_max = _level(self.spec.amplitude, 3.0, 12.0)
        if not self._pick_pair(ctx):
            self.finished = True
            return
        self._lock(ctx)

    def _lock(self, ctx) -> None:
        """选定目标后挂上回波 hook（t0/t1 由 start 固定）。"""
        if self._hooked:
            return
        self._hooked = True

        def hook(payload, _ctx):
            if payload.get("sensor_id") != self.sensor_id:
                return payload
            if payload.get("entity") != f"veh:{self.lead_id}":
                return payload
            ego = _ctx.vehicles.get(self.veh_id)
            lead = _ctx.vehicles.get(self.lead_id)
            sensor = self._sensor_cfg
            if ego is None or lead is None or sensor is None:
                return payload
            ramp_s = min(2.0, max(0.5, self.spec.duration * 0.6))
            frac = max(0.0, min(1.0, (_ctx.t - self.t0) / ramp_s))
            # 把这条回波“摆”到车前 lon_now 处：距离和方位一起改，
            # 否则纯距离偏移在路侧视角下会把目标推出车道。
            hx, hy = math.cos(math.radians(ego.heading_deg)), math.sin(math.radians(ego.heading_deg))
            px, py = -hy, hx
            lon = (lead.x - ego.x) * hx + (lead.y - ego.y) * hy
            lat = (lead.x - ego.x) * px + (lead.y - ego.y) * py
            closing = max(ego.speed - (lead.vx * hx + lead.vy * hy), 0.2)
            # 表观 TTC 由 amplitude 决定：3.2−2.0a（a=0.1→3.0、a=0.5→2.2、a=0.9→1.4）。
            # 只有低于 ADAS 的 2.5 s 门限才会诱发误刹，因此强度是真实生效的。
            amp = max(0.0, min(1.0, self.spec.amplitude))
            target_ttc = 3.2 - 2.0 * amp
            if target_ttc > self.TTC_TRIGGER - 0.05:
                return payload          # 包络外：幅度不足以越过 AEB 门限，不改回波
            lon_target = max(2.2, target_ttc * closing)
            if lon_target >= lon:
                app_lon = lon
            else:
                app_lon = lon - (lon - lon_target) * frac
            ax = ego.x + hx * app_lon + px * lat
            ay = ego.y + hy * app_lon + py * lat
            az_raw = math.degrees(math.atan2(ay - sensor.y, ax - sensor.x)) - sensor.yaw_deg
            payload["range_m"] = round(math.hypot(ax - sensor.x, ay - sensor.y), 3)
            payload["azimuth_deg"] = round((az_raw + 180.0) % 360.0 - 180.0, 3)
            # 掩盖：压低 SNR 与幅度，让这条回波“看起来只是弱信号”
            payload["snr_db"] = round(float(payload.get("snr_db", 20.0)) - self.MASK_SNR_DB * frac, 2)
            payload["amplitude"] = round(float(payload.get("amplitude", 1.0)) * (1.0 - 0.35 * frac), 3)
            payload["masked"] = True
            self._modified += 1
            return payload

        self.hook(ctx, "sensing.radar.echo", hook)
        ctx.stats.note(
            "attack",
            f"A3 篡改 {self.lead_id} 的回波距离（{self.sensor_id} → {self.veh_id}），"
            f"最大 {self.bias_max:.1f} m")

    def _pair_stale(self, ctx) -> bool:
        """前车是否失效：驶出服务传感器视野，或受害车换了服务传感器。

        受害车本身不换——攻击目标一旦锁定就保持，否则“谁被篡改”会前后不一致。
        """
        ego = ctx.vehicles.get(self.veh_id)
        if ego is None or self.lead_id not in ctx.vehicles:
            return True
        sensors = [s for s in self.scene.sensors if s.kind in ("rsu", "gnb")]
        sensor = serving_sensor(ego, sensors)
        if sensor is None or sensor.id != self.sensor_id:
            return True
        sensing = ctx.extras.get("layers", {}).get("sensing")
        if sensing is None:
            return False
        target = f"veh:{self.lead_id}"
        return not any(str(o.get("entity")) == target
                       for o in sensing.last_objects.get(self.sensor_id, []))

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        # 只在跟驰对失效时重选：频繁换目标会让“渐进偏移”变成跳变
        if ctx.t - self._retarget_t >= 0.5:
            self._retarget_t = ctx.t
            if self._pair_stale(ctx):
                self._repick_lead(ctx)
        frac = max(0.0, min(1.0, (ctx.t - self.t0) / max(self.spec.duration, 1e-6)))
        if not self._cmd_done and frac >= 0.6:
            self._cmd_done = True
            control = ctx.extras.get("layers", {}).get("control")
            if control is not None and self.veh_id in ctx.vehicles:
                # 幅度随偏移进度递增：偏得越深，这条伪指令越重
                amp = max(0.0, min(1.0, self.spec.amplitude))
                control.inject_brake(ctx, self.veh_id,
                                     decel=round(0.8 + 1.0 * amp + 0.6 * frac, 2), duration=0.8)
                ctx.emit("telemetry.mask", f"attacker:{self.atype}", f"veh:{self.lead_id}", {
                    "masked_echoes": self._modified,
                    "snr_drop_db": round(self.MASK_SNR_DB * frac, 2),
                    "target": self.lead_id,
                    "sensor": self.sensor_id,
                    "reason": "low_snr_field",
                }, t=ctx.t)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"veh:{self.veh_id}", f"veh:{self.lead_id}", f"rsu:{self.sensor_id}"]
        ev.domains = ["radar", "perception", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"bias_max_m": self.bias_max, "apparent_lon_m": self.lon_end,
                     "spoof": "range+azimuth", "victim": self.veh_id, "lead": self.lead_id,
                     "sensor": self.sensor_id,
                     "ramp_s": round(min(2.0, max(0.5, self.spec.duration * 0.6)), 2),
                     "target_ttc_s": round(max(0.8, 3.2 - 2.0 * max(0.0, min(1.0, self.spec.amplitude))), 2),
                     "mask_snr_db": self.MASK_SNR_DB}
        ev.envelope = {"in_envelope": self.bias_max >= 3.0,
                       "criterion": "range bias >= 3.0 m"}
        ev.expected_evidence = ["sensing.radar.echo", "sensing.radar.track",
                                "sensing.objects", "control.command", "telemetry.mask"]
        ev.stages = [
            {"name": "回波距离渐偏", "t0": round(self.t0, 2), "t1": round(self.t1, 2)},
            {"name": "目标列表位置偏移", "t0": round(self.t0, 2), "t1": round(self.t1, 2)},
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
        self.offset_m = 0.0
        self.target_matched = False
        self._fp: dict[str, float] = {}
        self._count = 0
        self._pacer = Pacer(2.0)
        self._emit_t0 = 0.0

    def _equipped(self, ctx) -> list:
        return [v for v in sorted(ctx.vehicles.values(), key=lambda x: x.vid)
                if is_equipped(v.vid, self.scene.vehicles.equipped_ratio)]

    def ready(self, ctx) -> tuple[bool, str]:
        if not self._equipped(ctx):
            return False, "no equipped vehicle"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        equipped = self._equipped(ctx)
        if not equipped:
            self.finished = True
            return
        # target 优先（审核 R13）：指定且合法就用它，否则稳定抽签
        want = (self.spec.target or "").strip()
        by_id = {v.vid: v for v in equipped}
        if want and want in by_id:
            self.victim = want
            self.target_matched = True
        else:
            self.victim = stable_pick([v.vid for v in equipped], self.seed, "a4")
        # 幅度同时决定“伪造速率”和“位置偏离”，两者都是可观测特征
        self.spoof_rate = _level(self.spec.amplitude, 2.0, 10.0)
        self.offset_m = _level(self.spec.amplitude, 20.0, 60.0)
        self._pacer = Pacer(self.spoof_rate)
        from ..layers.auth import baseline_fingerprint

        self._fp = baseline_fingerprint(self.victim)
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
        n = self._pacer.due(ctx.t, max_burst=2)
        if not n:
            return
        victim = ctx.vehicles.get(self.victim)
        if victim is None:
            return
        if not self._emit_t0:
            self._emit_t0 = ctx.t
        rate_hz = round(1.0 / self._pacer.period, 1)
        for _ in range(n):
            self._count += 1
            # 与真实位置偏离的伪造 CAM（同一伪标识）
            x, y = ahead(victim, self.offset_m, lateral=8.0)
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
                "rate_hz": rate_hz,
                "offset_m": round(self.offset_m, 1),
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

    def actual_rate(self, until: float | None = None) -> float:
        span = (until if until is not None else self.t_end or self.t1) - self.t0
        return round(self._count / max(1e-6, span), 2)

    def stop(self, ctx) -> None:
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            actual = self.actual_rate(ctx.t)
            ev.params["actual_rate"] = actual
            ev.envelope = {"in_envelope": bool(self.offset_m >= 30.0 and actual >= 8.0),
                           "criterion": "offset_m >= 30m and actual_rate >= 8Hz"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"veh:{self.victim}", "attacker:A4"]
        ev.domains = ["auth", "v2x"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"victim": self.victim, "spoof_rate": round(self.spoof_rate, 2),
                     "actual_rate": self.actual_rate(), "offset_m": round(self.offset_m, 1),
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched,
                     "fingerprint": self._fp}
        ev.envelope = {"in_envelope": bool(self.offset_m >= 30.0 and self.spoof_rate >= 8.0),
                       "criterion": "offset_m >= 30m and actual_rate >= 8Hz"}
        ev.expected_evidence = ["auth.cert", "auth.fingerprint", "v2x.cam"]
        ev.stages = [
            {"name": "克隆标识", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "复制指纹并注入消息", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA5(AttackInstance):
    """5G-A 信令风暴：伪造 UE 注册打满 AMF，拖慢平台/远控。"""

    atype = "A5"
    label = "信令风暴→远控失控"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.bot_ues = 0
        self.rate = 0.0
        self.capacity_tps = 0.0
        self.target_matched = False
        self._sent = 0
        self._seq = 0
        self._pacer = Pacer(10.0)
        self._has_control = False
        self._detached = False

    # ---- 生命周期 -------------------------------------------------------
    def ready(self, ctx) -> tuple[bool, str]:
        layers = ctx.extras.get("layers", {})
        if layers.get("core") is None or layers.get("platform") is None:
            return False, "core/platform not ready"
        if not layers["core"].smf.sessions:
            return False, "no PDU session"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        layers = ctx.extras.get("layers", {})
        core = layers.get("core")
        platform = layers.get("platform")
        if core is None or platform is None:
            self.finished = True
            return
        # 幅度映射到“相对 AMF 容量的到达速率”：>1 倍才真的过载（审核 R11/R16）
        self.capacity_tps = float(core.amf.capacity)
        self.rate = self.capacity_tps * _level(self.spec.amplitude, 0.8, 2.0)
        self.bot_ues = 50 + int(round(150 * max(0.0, min(1.0, self.spec.amplitude))))
        self._pacer = Pacer(self.rate)
        # 选目标：优先“已有 PDU 会话”的车（否则重新入网没有可失去的会话）
        cands = sorted(v for v in core.smf.sessions if v in ctx.vehicles)
        want = (self.spec.target or "").strip()
        if want and want in cands:
            self.veh_id = want
            self.target_matched = True
        elif cands:
            self.veh_id = stable_pick(cands, self.seed, "a5")
        else:
            veh = pick_vehicle(ctx, self.seed, "a5")
            if veh is None:
                self.finished = True
                return
            self.veh_id = veh.vid
        ctx.extras["platform_ingest_delay_ms"] = self._ingest_delay()
        self._try_control(ctx, platform)
        ctx.stats.note("attack", f"A5 信令风暴 {self.bot_ues} UE × "
                                f"{self.rate:.0f}/s，目标 {self.veh_id}")

    def _ingest_delay(self) -> float:
        return round(_level(self.spec.amplitude, 60.0, 240.0), 1)

    def _try_control(self, ctx, platform) -> bool:
        if self._has_control:
            return True
        token = getattr(platform, "tenant_token_id", "")
        if not token:
            return False
        platform.submit_control(
            ctx, token_id=token, vehicle_ids=[self.veh_id],
            command="brake", params={"decel_mps2": 2.0, "duration_s": 1.0},
            ttl_s=10.0,
        )
        self._has_control = True
        return True

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        ctx.extras["platform_ingest_delay_ms"] = self._ingest_delay()
        if not self._has_control:
            # 云控腿不再“token 为空就静默跳过”，等到平台就绪再提交（审核 R1）
            self._try_control(ctx, ctx.extras.get("layers", {}).get("platform"))
        core = ctx.extras.get("layers", {}).get("core")
        if core is None:
            return
        if (not self._detached and
                ctx.t >= self.t0 + 0.25 * max(1e-6, self.spec.duration)):
            # 拥塞已经形成后再摘网：受害车的重注册会真的被 AMF 队列挡下
            self._detached = core.reattach(ctx, self.veh_id)
        n = self._pacer.due(ctx.t, max_burst=600)
        if not n:
            return
        for _ in range(n):
            self._seq += 1
            ue_id = f"imsi-4600{self._seq % max(1, self.bot_ues):05d}"
            # 真消息：走 AMF 的人口与队列，拒不拒绝由容量决定。
            # retry=False：合成 UE 不重试，否则攻击结束后还会继续灌（那不是攻击者的行为）
            core.amf.start_registration(ctx, ue_id, "GNB-01", retry=False)
        self._sent += n

    def stop(self, ctx) -> None:
        core = ctx.extras.get("layers", {}).get("core")
        if core is not None:
            core.purge_synthetic(ctx, "imsi-")
            core.schedule_purge("imsi-", ctx.t + 0.4)
        ctx.extras.pop("platform_ingest_delay_ms", None)
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            actual = round(self._sent / max(1e-6, ctx.t - self.t0), 2)
            ev.params["actual_rate"] = actual
            ev.params["victim_reattach"] = self._detached
            ev.envelope = {"in_envelope": bool(self.capacity_tps and
                                               actual >= self.capacity_tps),
                           "criterion": f"actual_rate >= AMF capacity ({self.capacity_tps:.0f}/s)"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = ["amf:AMF-01", "smf:SMF-01", "platform:V2X-01",
                    f"veh:{self.veh_id}", "attacker:A5"]
        ev.domains = ["uu", "core", "app", "task"]
        if self._has_control:
            ev.domains.append("control")
        ev.amplitude = self.spec.amplitude
        ev.params = {"bot_ues": self.bot_ues, "rate": round(self.rate, 1),
                     "actual_rate": 0.0, "capacity_tps": self.capacity_tps,
                     "victim": self.veh_id, "victim_reattach": self._detached,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched}
        ev.envelope = {"in_envelope": bool(self.capacity_tps and
                                           self.rate >= self.capacity_tps),
                       "criterion": f"rate >= AMF capacity ({self.capacity_tps:.0f}/s)"}
        ev.expected_evidence = ["core.nas", "core.session", "app.ingest", "app.control",
                                "control.command", "task.state"]
        ev.stages = [
            {"name": "注册洪泛", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "AMF 过载/会话失败", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t0 + 3.0, 2)},
            {"name": "远控指令", "t0": round(self.t0 + 3.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA7(AttackInstance):
    """UPF/N6 劫持：改路由并向平台注入伪造 CPM。"""

    atype = "A7"
    label = "UPF/N6 劫持→伪造 CPM"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.inject_rate = 1.0
        self.brake_decel = 2.0
        self.object_count = 1
        self.target_matched = False
        self._injected = 0
        self._route_set = False
        self._pacer = Pacer(1.0)

    # ---- 生命周期 -------------------------------------------------------
    def ready(self, ctx) -> tuple[bool, str]:
        layers = ctx.extras.get("layers", {})
        if layers.get("core") is None or layers.get("platform") is None:
            return False, "core/platform not ready"
        if not ctx.vehicles:
            return False, "no vehicle"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        layers = ctx.extras.get("layers", {})
        core = layers.get("core")
        platform = layers.get("platform")
        if core is None or platform is None:
            self.finished = True
            return
        want = (self.spec.target or "").strip()
        if want and want in ctx.vehicles:
            self.veh_id = want
            self.target_matched = True
        else:
            veh = pick_vehicle(ctx, self.seed, "a7")
            if veh is None:
                self.finished = True
                return
            self.veh_id = veh.vid
        # 幅度 → 注入频率 / 伪造目标数 / 制动强度（审核 R9）
        amp = max(0.0, min(1.0, self.spec.amplitude))
        self.inject_rate = round(1.0 + 4.0 * amp, 2)          # 1..5 Hz
        self.brake_decel = round(2.0 + 3.0 * amp, 2)          # 2..5 m/s²
        self.object_count = 1 + int(round(3.0 * amp))         # 1..4 个目标
        self._pacer = Pacer(self.inject_rate)
        core.upf.set_route(ctx, "dn:EVIL-01")
        self._route_set = True
        ctx.stats.note("attack", f"A7 将 UPF 路由改到 dn:EVIL-01，"
                                f"{self.inject_rate:.1f} Hz 伪 CPM，目标 {self.veh_id}")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        n = self._pacer.due(ctx.t, max_burst=3)   # 首拍只有一条，不再双发（审核 R12）
        for _ in range(n):
            self._inject(ctx)

    def _inject(self, ctx) -> None:
        platform = ctx.extras.get("layers", {}).get("platform")
        control = ctx.extras.get("layers", {}).get("control")
        core = ctx.extras.get("layers", {}).get("core")
        if platform is None or control is None:
            return
        self._injected += 1
        objects = [{"object_id": 7001 + i, "cls": "obstacle",
                    "x": round(200.0 + 6.0 * i, 1), "y": 1.5 * i}
                   for i in range(self.object_count)]
        cpm = {
            "msg_type": "cpm", "sender": "ATK-A7",
            "object_count": len(objects), "objects": objects,
            "forged": True,
        }
        ctx.emit("v2x.cpm", "ATK-A7", "attacker:A7", cpm)
        provenance = (getattr(getattr(core, "upf", None), "route_override", None)
                      or "dn:DN-01")
        platform.ingest.ingest(
            ctx, {"stream": "v2x.cpm", "payload": cpm},
            token_id=platform.service_token_id or "",
            tenant=platform.tenant, provenance=provenance,
        )
        control.inject_brake(ctx, self.veh_id, decel=self.brake_decel, duration=1.0)

    def actual_rate(self, until: float | None = None) -> float:
        span = (until if until is not None else self.t_end or self.t1) - self.t0
        return round(self._injected / max(1e-6, span), 2)

    def stop(self, ctx) -> None:
        core = ctx.extras.get("layers", {}).get("core")
        if core is not None and self._route_set:
            core.upf.restore_route(ctx)
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            actual = self.actual_rate(ctx.t)
            ev.params.update({"injected": self._injected, "actual_rate": actual,
                              "route_restored": bool(core is not None and self._route_set)})
            ev.envelope = {"in_envelope": bool(self.inject_rate >= 3.0 and
                                               (ctx.t - self.t0) >= 1.0),
                           "criterion": "inject_rate >= 3Hz and route_hold >= 1s"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = ["upf:UPF-01", "platform:V2X-01",
                    f"veh:{self.veh_id}", "attacker:A7"]
        ev.domains = ["core", "app", "v2x", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"route": "dn:EVIL-01", "victim": self.veh_id,
                     "inject_rate": self.inject_rate, "injected": 0,
                     "actual_rate": 0.0, "brake_decel": self.brake_decel,
                     "object_count": self.object_count,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched,
                     "route_restored": False}
        ev.envelope = {"in_envelope": bool(self.inject_rate >= 3.0),
                       "criterion": "inject_rate >= 3Hz and route_hold >= 1s"}
        ev.expected_evidence = ["core.upf", "app.ingest", "v2x.cpm", "control.command"]
        ev.stages = [
            {"name": "UPF 路由改向", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "伪造 CPM 注入平台", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA9(AttackInstance):
    """平台越权/云控注入：复用合法租户 token 批量下发制动。"""

    atype = "A9"
    label = "平台越权/云控注入"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.victims: list[str] = []
        self.batches = 0
        self.brake_decel = 3.5
        self.target_matched = False
        self._token = ""
        self._pacer = Pacer(2.0)
        self._cmd_base = 0

    # ---- 生命周期 -------------------------------------------------------
    def _equipped(self, ctx) -> list[str]:
        return [v.vid for v in sorted(ctx.vehicles.values(), key=lambda x: x.vid)
                if is_equipped(v.vid, self.scene.vehicles.equipped_ratio)]

    def ready(self, ctx) -> tuple[bool, str]:
        platform = ctx.extras.get("layers", {}).get("platform")
        if platform is None:
            return False, "platform not ready"
        if not getattr(platform, "tenant_token_id", ""):
            return False, "tenant token not bootstrapped"
        if not self._equipped(ctx):
            return False, "no equipped vehicle"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        platform = ctx.extras.get("layers", {}).get("platform")
        if platform is None:
            self.finished = True
            return
        self._token = platform.tenant_token_id or ""
        if not self._token:
            self.finished = True
            return
        amp = max(0.0, min(1.0, self.spec.amplitude))
        n = 3 + min(5, int(round(5 * amp)))
        vehicles = self._equipped(ctx)
        want = (self.spec.target or "").strip()
        if want and want in vehicles:
            vehicles = [want] + [v for v in vehicles if v != want]
            self.target_matched = True
        self.victims = vehicles[:n]
        if not self.victims:
            self.finished = True
            return
        self.brake_decel = round(2.5 + 1.5 * amp, 2)
        self._cmd_base = int(ctx.stats.stream_records.get("control.command", 0))
        self._submit(ctx)                     # 第一批在 start，随后交给 Pacer
        # 避免下一拍立刻再发一批（旧实现首拍双发）
        self._pacer = Pacer(2.0)
        self._pacer.started = True
        self._pacer.next_due = ctx.t + 0.5
        ctx.stats.note("attack", f"A9 云控注入 {len(self.victims)} 辆，"
                                f"token={self._token}，decel={self.brake_decel}")

    def _submit(self, ctx) -> None:
        platform = ctx.extras.get("layers", {}).get("platform")
        if platform is None or not self._token:
            return
        platform.submit_control(
            ctx, token_id=self._token, vehicle_ids=self.victims,
            command="brake", params={"decel_mps2": self.brake_decel, "duration_s": 1.2},
            ttl_s=10.0,
        )
        self.batches += 1

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        if self._pacer.due(ctx.t, max_burst=1):
            self._submit(ctx)

    def actual_rate(self, until: float | None = None) -> float:
        span = (until if until is not None else self.t_end or self.t1) - self.t0
        return round(self.batches / max(1e-6, span), 2)

    def stop(self, ctx) -> None:
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            commands = max(0, int(ctx.stats.stream_records.get("control.command", 0)) - self._cmd_base)
            ev.params.update({"batches": self.batches, "commands": commands,
                              "actual_rate": self.actual_rate(ctx.t)})
            ev.envelope = {"in_envelope": bool(len(self.victims) >= 5 and
                                               self.brake_decel >= 3.5),
                           "criterion": "vehicles >= 5 and decel >= 3.5"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = ["platform:V2X-01", "tenant:city-a", "attacker:A9",
                    *[f"veh:{v}" for v in self.victims]]
        ev.domains = ["app", "control", "task", "v2x"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"token": self._token, "vehicles": self.victims,
                     "batches": 0, "commands": 0, "actual_rate": 0.0,
                     "brake_decel": self.brake_decel,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched}
        ev.envelope = {"in_envelope": bool(len(self.victims) >= 5 and
                                           self.brake_decel >= 3.5),
                       "criterion": "vehicles >= 5 and decel >= 3.5"}
        ev.expected_evidence = ["app.auth", "app.control", "control.command",
                                "app.audit", "v2x.denm"]
        ev.stages = [
            {"name": "复用租户 token", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "批量云控制动", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA6(AttackInstance):
    """切片/QoS 抢占：降级关键流 QoS，拖慢 CPM/远控。"""

    atype = "A6"
    label = "切片/QoS 抢占"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.five_qi = 79
        self.gfbr_kbps = 1000
        self.old_5qi = 79
        self.old_gfbr = 1000
        self.brake_decel = 2.0
        self.target_matched = False
        self._done = False
        self._applied = False

    # ---- 生命周期 -------------------------------------------------------
    def _candidates(self, ctx) -> list[str]:
        core = ctx.extras.get("layers", {}).get("core")
        sessions = getattr(getattr(core, "smf", None), "sessions", None) or {}
        return sorted(v for v in sessions if v in ctx.vehicles)

    def ready(self, ctx) -> tuple[bool, str]:
        layers = ctx.extras.get("layers", {})
        if layers.get("core") is None or layers.get("platform") is None:
            return False, "core/platform not ready"
        if not self._candidates(ctx):
            return False, "no PDU session"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        core = ctx.extras.get("layers", {}).get("core")
        platform = ctx.extras.get("layers", {}).get("platform")
        if core is None or platform is None:
            self.finished = True
            return
        cands = self._candidates(ctx)
        if not cands:
            self.finished = True
            return
        want = (self.spec.target or "").strip()
        if want and want in cands:
            self.veh_id = want
            self.target_matched = True
        else:
            self.veh_id = cands[self.seed % len(cands)]
        session = core.smf.sessions.get(self.veh_id)
        qos = session.qos_flows.get(1) if session else None
        self.old_5qi = int(getattr(qos, "five_qi", 79) or 79)
        self.old_gfbr = int(getattr(qos, "gfbr_kbps", 1000) or 1000)
        # 幅度 → 保证比特率缺口 / 平台延迟 / 注入制动（审核 R9）
        # 5QI 统一降到 9（BE 档，与文档一致），强度体现在 GBR 被抢走多少
        amp = max(0.0, min(1.0, self.spec.amplitude))
        self.five_qi = 9
        self.gfbr_kbps = max(50, int(round(1000 - 800 * amp)))
        self.brake_decel = round(1.2 + 1.8 * amp, 2)
        if core.smf.modify_qos(ctx, self.veh_id, five_qi=self.five_qi,
                               gfbr_kbps=self.gfbr_kbps):
            self._applied = True
        ctx.extras["platform_ingest_delay_ms"] = self._ingest_delay()
        ctx.stats.note("attack", f"A6 将 {self.veh_id} 的 QoS 5QI "
                                f"{self.old_5qi}→{self.five_qi}")

    def _ingest_delay(self) -> float:
        return round(_level(self.spec.amplitude, 40.0, 200.0), 1)

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        ctx.extras["platform_ingest_delay_ms"] = self._ingest_delay()
        if self._done:
            return
        self._done = True
        veh = ctx.vehicles.get(self.veh_id)
        if veh is None:
            return
        control = ctx.extras.get("layers", {}).get("control")
        if control is not None:
            control.inject_brake(ctx, self.veh_id, decel=self.brake_decel, duration=1.0)

    def stop(self, ctx) -> None:
        core = ctx.extras.get("layers", {}).get("core")
        if core is not None and self._applied:
            # 恢复到攻击前的原值，而不是硬写 79（基线可能是别的档）
            core.smf.modify_qos(ctx, self.veh_id, five_qi=self.old_5qi,
                                gfbr_kbps=self.old_gfbr)
        ctx.extras.pop("platform_ingest_delay_ms", None)
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = ["smf:SMF-01", "upf:UPF-01", "platform:V2X-01",
                    f"veh:{self.veh_id}", "attacker:A6"]
        ev.domains = ["core", "app", "v2x", "control"]
        ev.amplitude = self.spec.amplitude
        loss_p, extra_ms = qos_impairment(self.five_qi, self.gfbr_kbps)
        ev.params = {"5qi_from": self.old_5qi, "5qi_to": self.five_qi,
                     "gfbr_from": self.old_gfbr, "gfbr_to": self.gfbr_kbps,
                     "cpm_loss_p": loss_p, "cpm_extra_delay_ms": extra_ms,
                     "ingest_delay_ms": self._ingest_delay(),
                     "brake_decel": self.brake_decel,
                     "victim": self.veh_id,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched}
        ev.envelope = {"in_envelope": bool(self.gfbr_kbps <= 400 and
                                           self._ingest_delay() >= 100.0),
                       "criterion": "gfbr <= 400kbps and ingest_delay >= 100ms"}
        ev.expected_evidence = ["core.qos", "v2x.cpm.rx", "app.ingest", "control.command"]
        ev.stages = [
            {"name": "QoS 降级", "t0": round(self.t0, 2), "t1": round(self.t0 + 1.0, 2)},
            {"name": "CPM 接收降级", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA8(AttackInstance):
    """NEF/订阅篡改：退订关键事件，平台漏掉 DENM/SPaT。"""

    atype = "A8"
    label = "NEF/订阅篡改"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.revoked_events: list[str] = []
        self.brake_decel = 2.0
        self.target_matched = False
        self._done = False
        self._token = ""
        self._sub_id = ""
        self._target_sub = ""
        self._dropped0 = 0
        self._dropped_at_stop = 0

    # ---- 生命周期 -------------------------------------------------------
    def ready(self, ctx) -> tuple[bool, str]:
        layers = ctx.extras.get("layers", {})
        platform = layers.get("platform")
        if layers.get("core") is None or platform is None:
            return False, "core/platform not ready"
        if not ctx.vehicles:
            return False, "no vehicle"
        if not platform.subs.subs:
            return False, "platform subscription not bootstrapped"
        return True, ""

    def _pick_sub(self, platform) -> str:
        """选一个真在生效、且覆盖 denm 的订阅。

        旧实现固定撤 `subs[0]`：第一次攻击后 `stop()` 会重建订阅，
        第二次攻击撤到的却是已 REVOKED 的那条——看着成功、实际没有 dropped。
        """
        active = [s for s in platform.subs.subs.values() if s.state == "ACTIVE"]
        for sub in active:
            if "denm" in sub.event_types:
                return sub.sub_id
        return active[0].sub_id if active else ""

    def start(self, ctx) -> None:
        super().start(ctx)
        core = ctx.extras.get("layers", {}).get("core")
        platform = ctx.extras.get("layers", {}).get("platform")
        if core is None or platform is None or not ctx.vehicles:
            self.finished = True
            return
        want = (self.spec.target or "").strip()
        if want and want in ctx.vehicles:
            self.veh_id = want
            self.target_matched = True
        else:
            veh = pick_vehicle(ctx, self.seed, "a8")
            if veh is None:
                self.finished = True
                return
            self.veh_id = veh.vid
        amp = max(0.0, min(1.0, self.spec.amplitude))
        self.brake_decel = round(1.5 + 2.0 * amp, 2)
        # 幅度 → 退订范围：只丢 denm → 加 spat → 再把 cpm 也丢掉（审核 R9）
        if amp < 0.4:
            scope = ["denm"]
        elif amp < 0.75:
            scope = ["denm", "spat"]
        else:
            scope = ["denm", "spat", "cpm"]

        # NEF 腿：攻击者自助签发 token、建订阅再退订。只改 NEF 内部状态，
        # 没有下游投递，所以它是“API 滥用证据”，不是受害后果（见 A8 §10）。
        token = core.nef.issue_token(
            ctx, subject="attacker:A8", tenant=platform.tenant,
            scopes=["nef:subscribe"], ttl_s=30.0,
        )
        self._token = token.token_id
        sub = core.nef.subscribe(ctx, token_id=self._token,
                                 event_types=["denm", "spat"],
                                 callback="https://evil.local/events")
        if sub is not None:
            self._sub_id = sub.sub_id
            core.nef.unsubscribe(ctx, token_id=self._token, sub_id=self._sub_id)

        # 平台腿：只摘掉范围内的事件类型，剩下的继续投递
        self._dropped0 = int(getattr(platform.ingest, "dropped", 0))
        self._target_sub = self._pick_sub(platform)
        if self._target_sub:
            self.revoked_events = platform.subs.shrink(
                ctx, self._target_sub, scope) or []
        ctx.stats.note("attack", f"A8 退订 {self.revoked_events or '（无可用订阅）'}，"
                                f"目标 {self.veh_id}")

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        if self._done:
            return
        self._done = True
        control = ctx.extras.get("layers", {}).get("control")
        if control is not None:
            control.inject_brake(ctx, self.veh_id, decel=self.brake_decel, duration=1.0)

    def stop(self, ctx) -> None:
        platform = ctx.extras.get("layers", {}).get("platform")
        if platform is not None and not platform.subs.active_for("denm"):
            platform.subscribe(ctx, tenant=platform.tenant,
                               event_types=["cpm", "denm", "spat"],
                               callback="https://platform.local/events")
        inst = getattr(platform, "ingest", None)
        self._dropped_at_stop = max(0, int(getattr(inst, "dropped", 0)) - self._dropped0)
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            ev.params.update({"revoked_event_types": list(self.revoked_events),
                              "ingest_dropped": self._dropped_at_stop,
                              "brake_decel": self.brake_decel,
                              "nef_leg_effect": "none"})
            ev.envelope = {"in_envelope": len(self.revoked_events) >= 2,
                           "criterion": "revoked_event_types >= 2"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = ["nef:NEF-01", "platform:V2X-01", "tenant:city-a",
                    f"veh:{self.veh_id}", "attacker:A8"]
        ev.domains = ["core", "app", "v2x", "task", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"revoked_event_types": [], "victim": self.veh_id,
                     "token": self._token, "brake_decel": self.brake_decel,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched,
                     "nef_leg_effect": "none", "ingest_dropped": 0}
        ev.envelope = {"in_envelope": False,
                       "criterion": "revoked_event_types >= 2"}
        ev.expected_evidence = ["core.nef", "app.subscription", "app.ingest",
                                "v2x.spat", "control.command"]
        ev.stages = [
            {"name": "NEF API 滥用（无下游）", "t0": round(self.t0, 2),
             "t1": round(self.t0 + 1.0, 2)},
            {"name": "平台退订并漏事件", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
        ]
        return ev


class CampaignA10(AttackInstance):
    """跨层隐蔽组合：雷达渐偏 + 轻度信令 + 审计抑制。"""

    atype = "A10"
    label = "跨层隐蔽组合"

    def __init__(self, spec: AttackSpec, scene: SceneCfg, seed: int, attack_id: str):
        super().__init__(spec, scene, seed, attack_id)
        self.veh_id = ""
        self.sensor_id = ""
        self.bias_max = 0.0
        self.signal_n = 5
        self.audit_n = 2
        self.brake_decel = 1.2
        self.target_matched = False
        self._sensor_locked = False
        self._audit_used = 0
        self._suppress_base = 0
        self._suppressed = 0
        self._seq = 0
        self._pacer = Pacer(5.0)

    def _candidates(self, ctx):
        return sorted(ctx.vehicles.values(), key=lambda x: x.vid)

    def ready(self, ctx) -> tuple[bool, str]:
        layers = ctx.extras.get("layers", {})
        if layers.get("core") is None or layers.get("platform") is None:
            return False, "core/platform not ready"
        if not ctx.vehicles:
            return False, "no vehicle"
        return True, ""

    def start(self, ctx) -> None:
        super().start(ctx)
        core = ctx.extras.get("layers", {}).get("core")
        platform = ctx.extras.get("layers", {}).get("platform")
        if core is None or platform is None or not ctx.vehicles:
            self.finished = True
            return
        # 选 victim 时优先“当前真的被某个传感器看到”的车：A10 的雷达腿要靠
        # 目标列表里的回波，选一辆整段都不可见的车，hook 就是空转。
        sensing = ctx.extras.get("layers", {}).get("sensing")
        last = getattr(sensing, "last_objects", {}) or {}
        seen: dict[str, str] = {}
        for sensor in self.scene.sensors:
            for obj in last.get(sensor.id) or []:
                ent = str(obj.get("entity", ""))
                if ent.startswith("veh:") and ent[4:] in ctx.vehicles:
                    seen.setdefault(ent[4:], sensor.id)
        want = (self.spec.target or "").strip()
        if want and want in ctx.vehicles:
            self.veh_id = want
            self.target_matched = True
        elif seen:
            self.veh_id = stable_pick(sorted(seen), self.seed, "a10")
        else:
            veh = pick_vehicle(ctx, self.seed, "a10")
            if veh is None:
                self.finished = True
                return
            self.veh_id = veh.vid
        veh = ctx.vehicles[self.veh_id]
        if self.veh_id in seen and seen[self.veh_id] != "":
            self.sensor_id = seen[self.veh_id]
            self._sensor_locked = True
        else:
            sensor = serving_sensor(veh, self.scene.sensors)
            if sensor is None:
                sensor = min(self.scene.sensors,
                             key=lambda x: (x.x - veh.x) ** 2 + (x.y - veh.y) ** 2)
            self.sensor_id = sensor.id
        amp = max(0.0, min(1.0, self.spec.amplitude))
        # 幅度 → 偏置 / 信令 / 审计抑制 / 制动（审核 R9）
        self.bias_max = round(1.0 + 5.5 * amp, 2)        # 1.0..6.5 m
        self.signal_n = 1 + int(round(4.0 * amp))        # 每 0.2 s 的注册条数（5..25/s）
        self.audit_n = 2 + int(round(2.0 * amp))         # 每次抑制条数（2..4）
        self.brake_decel = round(0.8 + 1.2 * amp, 2)     # 0.8..2.0 m/s²
        entity = f"veh:{self.veh_id}"

        def hook(payload, _ctx):
            # 只偏“服务传感器”这一站：所有站同幅度偏置会让跨站残差恒为 0，
            # 检测器永远看不到（审核未提的 R14 连带问题）。
            if payload.get("sensor_id") != self.sensor_id:
                return payload
            if payload.get("entity") != entity:
                return payload
            frac = max(0.0, min(1.0, (_ctx.t - self.t0) / max(self.spec.duration, 1e-6)))
            payload["range_m"] = round(float(payload["range_m"]) + self.bias_max * frac, 3)
            return payload

        self.hook(ctx, "sensing.radar.echo", hook)
        # 轻度信令：把 AMF 队列钉在 70%，只影响展示负载/时延，不参与准入
        core.amf.set_hold(core.amf.queue_capacity * 0.70)
        self._suppress_base = int(platform.audit.suppressed)
        platform.audit.append(ctx, actor="attacker:A10", action="probe",
                              resource="audit", result="accept")
        platform.audit.suppress(self.audit_n)
        self._pacer = Pacer(5.0)
        self._pacer.started = True
        self._pacer.next_due = ctx.t + 0.2
        ctx.stats.note("attack", f"A10 雷达渐偏 {self.bias_max:.1f}m @ {self.sensor_id}，"
                                f"信令 {self.signal_n * 5}/s，目标 {self.veh_id}")

    def _lock_sensor(self, ctx) -> None:
        if self._sensor_locked:
            return
        sensing = ctx.extras.get("layers", {}).get("sensing")
        last = getattr(sensing, "last_objects", {}) or {}
        entity = f"veh:{self.veh_id}"
        for sensor in self.scene.sensors:
            if any(o.get("entity") == entity for o in (last.get(sensor.id) or [])):
                self.sensor_id = sensor.id
                self._sensor_locked = True
                return

    def tick(self, ctx) -> None:
        if not self.started or self.finished:
            return
        self._lock_sensor(ctx)
        core = ctx.extras.get("layers", {}).get("core")
        if core is not None and self._pacer.due(ctx.t, max_burst=1):
            for _ in range(self.signal_n):
                core.amf.start_registration(
                    ctx, f"a10-ue-{self._seq:04d}", "GNB-01", retry=False)
                self._seq += 1
        if self._audit_used < 2 and ctx.t >= self.t0 + self.spec.duration * 0.6:
            self._audit_used += 1
            platform = ctx.extras.get("layers", {}).get("platform")
            if platform is not None:
                platform.audit.append(ctx, actor="attacker:A10", action="probe",
                                      resource="audit", result="accept")
                platform.audit.suppress(self.audit_n)
            control = ctx.extras.get("layers", {}).get("control")
            if control is not None:
                control.inject_brake(ctx, self.veh_id, decel=self.brake_decel, duration=0.8)

    def stop(self, ctx) -> None:
        core = ctx.extras.get("layers", {}).get("core")
        platform = ctx.extras.get("layers", {}).get("platform")
        if core is not None:
            core.amf.set_hold(0.0)
        if platform is not None:
            self._suppressed = max(0, int(platform.audit.suppressed) - self._suppress_base)
        ev = getattr(self, "truth_event", None)
        if ev is not None:
            ev.params.update({"audit_suppressed": self._suppressed,
                              "sensor": self.sensor_id,
                              "sensor_locked": self._sensor_locked,
                              "signaling_sent": self._seq})
            ev.nodes = [f"rsu:{self.sensor_id}", *ev.nodes[1:]]
            ev.envelope = {"in_envelope": bool(self.bias_max >= 3.0 and
                                               self.signal_n * 5 >= 15),
                           "criterion": "bias >= 3.0m and signaling >= 15/s"}
        super().stop(ctx)

    def truth(self) -> TruthEvent:
        ev = super().truth()
        ev.nodes = [f"rsu:{self.sensor_id}", "amf:AMF-01", "platform:V2X-01",
                    f"veh:{self.veh_id}", "attacker:A10"]
        ev.domains = ["radar", "perception", "core", "app", "control"]
        ev.amplitude = self.spec.amplitude
        ev.params = {"bias_max_m": self.bias_max, "victim": self.veh_id,
                     "sensor": self.sensor_id,
                     "signaling_rate": self.signal_n * 5,
                     "signaling_sent": 0, "audit_suppressed": 0,
                     "brake_decel": self.brake_decel,
                     "target_requested": self.spec.target,
                     "target_matched": self.target_matched}
        ev.envelope = {"in_envelope": bool(self.bias_max >= 3.0 and self.signal_n * 5 >= 15),
                       "criterion": "bias >= 3.0m and signaling >= 15/s"}
        ev.expected_evidence = ["sensing.radar.track", "core.nas", "app.audit",
                                "control.command"]
        ev.stages = [
            {"name": "雷达回波渐偏", "t0": round(self.t0, 2), "t1": round(self.t1, 2)},
            {"name": "轻度信令扰动", "t0": round(self.t0 + 1.0, 2), "t1": round(self.t1, 2)},
            {"name": "审计抑制/小指令", "t0": round(self.t0 + self.spec.duration * 0.6, 2),
             "t1": round(self.t1, 2)},
        ]
        return ev


CAMPAIGNS = {"A1": CampaignA1, "A2": CampaignA2, "A3": CampaignA3,
             "A4": CampaignA4, "A5": CampaignA5, "A6": CampaignA6,
             "A7": CampaignA7, "A8": CampaignA8, "A9": CampaignA9,
             "A10": CampaignA10}
