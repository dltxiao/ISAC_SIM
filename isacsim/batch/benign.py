"""正常集里的合法运维动作（审核 R5 的难负样本）。

这些动作都**合法**，但形状和攻击很像：QoS 变更、N6 主备割接、订阅变更、
token 轮换、批量车队控制、真实急刹。正常集如果从不产生它们，测出来的误检率
只是“系统没见过这类操作”的下界。

只在批量生成器（`isacsim batch`）的正常场景里启用；交互式运行默认不注入。
"""
from __future__ import annotations

from typing import Any

from ..core.rng import rng_for


class BenignOps:
    """按时间表执行一组合法运维动作；每个动作只做一次。"""

    def __init__(self, seed: int, scene, duration: float) -> None:
        d = max(4.0, float(duration))
        rng = rng_for(seed, "benign-ops")
        jitter = lambda: float(rng.uniform(-0.2, 0.2))  # noqa: E731
        self.plan = {
            "qos": (d * 0.20 + jitter(), "qos"),
            "qos_restore": (d * 0.32 + jitter(), "qos_restore"),
            "route": (d * 0.40 + jitter(), "route"),
            "route_restore": (d * 0.50 + jitter(), "route_restore"),
            "unsub": (d * 0.55 + jitter(), "unsub"),
            "resub": (d * 0.65 + jitter(), "resub"),
            "rotate": (d * 0.70 + jitter(), "rotate"),
            "fleet": (d * 0.75 + jitter(), "fleet"),
            "brake": (d * 0.85 + jitter(), "brake"),
        }
        self._done: set[str] = set()
        self._token = ""
        self._old_token = ""
        self._sub_id = ""
        self._ue = ""
        self._old_qos: tuple[int, int] | None = None
        self.actions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        for name, (at, _) in sorted(self.plan.items(), key=lambda kv: kv[1][0]):
            if name in self._done or ctx.t + 1e-9 < at:
                continue
            try:
                self._do(ctx, name)
            except Exception:          # 正常集的运维动作失败不应打断仿真
                pass
            self._done.add(name)

    # ------------------------------------------------------------------
    def _do(self, ctx, name: str) -> None:
        layers = ctx.extras.get("layers", {})
        core = layers.get("core")
        platform = layers.get("platform")
        control = layers.get("control")
        if core is None or platform is None:
            return
        if name == "qos":
            ues = sorted(u for u in core.smf.sessions if u in ctx.vehicles)
            if not ues:
                return
            self._ue = ues[0]
            qos = core.smf.sessions[self._ue].qos_flows.get(1)
            self._old_qos = (int(getattr(qos, "five_qi", 79) or 79),
                             int(getattr(qos, "gfbr_kbps", 1000) or 1000))
            # 合法的切片调整：5QI 79→8、GFBR 1000→800（不是“抢占”）
            core.smf.modify_qos(ctx, self._ue, five_qi=8, gfbr_kbps=800)
            self.actions.append({"t": round(ctx.t, 2), "op": "qos_change",
                                 "ue": self._ue})
        elif name == "qos_restore" and self._ue and self._old_qos:
            core.smf.modify_qos(ctx, self._ue, five_qi=self._old_qos[0],
                                gfbr_kbps=self._old_qos[1])
            self.actions.append({"t": round(ctx.t, 2), "op": "qos_restore",
                                 "ue": self._ue})
        elif name == "route":
            # 合法主备割接：DN-01 → DN-02（在 allowed_dns 白名单里）
            core.upf.set_route(ctx, "dn:DN-02")
            self.actions.append({"t": round(ctx.t, 2), "op": "route_switch",
                                 "route": "dn:DN-02"})
        elif name == "route_restore":
            core.upf.restore_route(ctx)
            self.actions.append({"t": round(ctx.t, 2), "op": "route_restore"})
        elif name == "unsub":
            active = [s for s in platform.subs.subs.values()
                      if s.state == "ACTIVE" and "spat" in s.event_types]
            if not active:
                return
            self._sub_id = active[0].sub_id
            platform.subs.shrink(ctx, self._sub_id, ["spat"])   # 只退 1 类
            self.actions.append({"t": round(ctx.t, 2), "op": "unsubscribe_spat"})
        elif name == "resub" and self._sub_id:
            platform.subs.add_events(ctx, self._sub_id, ["spat"])
            self.actions.append({"t": round(ctx.t, 2), "op": "resubscribe_spat"})
        elif name == "rotate":
            token = platform.iam.issue(
                ctx, subject="ops:noc", tenant=platform.tenant,
                scopes=["control:write"], ttl_s=120.0)
            if self._token:
                platform.iam.revoke(ctx, self._token)
            self._old_token, self._token = self._token, token.token_id
            self.actions.append({"t": round(ctx.t, 2), "op": "token_rotate",
                                 "token": self._token})
        elif name == "fleet" and self._token:
            # 合法车队控制：单批 4 辆、带前序危险 TTC（不是无依据的 99）
            vids = [v.vid for v in sorted(ctx.vehicles.values(), key=lambda x: x.vid)][:4]
            if not vids:
                return
            platform.submit_control(
                ctx, token_id=self._token, vehicle_ids=vids, command="brake",
                params={"decel_mps2": 1.0, "duration_s": 1.0, "ttc_s": 1.5},
                ttl_s=10.0,
            )
            self.actions.append({"t": round(ctx.t, 2), "op": "fleet_control",
                                 "vehicles": len(vids)})
        elif name == "brake" and control is not None and ctx.vehicles:
            veh = sorted(ctx.vehicles.values(), key=lambda x: x.vid)[0]
            # 真实急刹：TTC 1.4 s（<10），会产生 DENM，不应触发 brake_without_hazard
            control._command(ctx, veh, ttc=1.4, reason="ttc",
                             source=f"adas:{veh.vid}", decel=3.0, duration=1.0)
            self.actions.append({"t": round(ctx.t, 2), "op": "real_brake",
                                 "veh": veh.vid})

    def snapshot(self) -> dict[str, Any]:
        return {"ops": list(self.actions), "token": self._token}
