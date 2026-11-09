"""CoreLayer：把 AMF/SMF/UPF/UDM/AUSF 编排成一个语义核心网。

不依赖真实网络；所有过程在进程内通过 CoreBus 推进。
"""
from __future__ import annotations

from collections import deque

from ...config import SceneCfg
from ...core.ids import SpanFactory
from ..defense import emit_defense
from ..health import attach_health, compute_health
from .amf import AMF
from .ausf import AUSF
from .bus import CoreBus
from .nef import NEF
from .smf import SMF
from .udm import UDM
from .upf import UPF


class CoreLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.cfg = scene.core
        self.factory = SpanFactory()
        self.bus = CoreBus()
        self.udm = UDM(scene, self.factory)
        self.ausf = AUSF(scene, self.udm, self.factory)
        self.amf = AMF(scene, self.bus, self.ausf, self.factory)
        self.smf = SMF(scene, self.bus, self.amf, self.factory)
        self.upf = UPF(scene, self.factory)
        self.nef = NEF(scene, self.factory)
        self.nfs = {
            self.amf.entity: self.amf,
            self.smf.entity: self.smf,
            self.upf.entity: self.upf,
        }
        self._known: dict[str, str] = {}
        self.deferred_purge: dict[str, float] = {}   # 前缀 → 清理截止时刻
        self._closed = False
        # 健康度看“最近 2 s 的错误率”，不是自开局以来的累计值：
        # 否则 A5 这类一次性风暴会让 AMF 永远红着（审核反馈）。
        self._t = 0.0
        self._err_prev: dict[str, tuple[int, int]] = {}
        self._err_win: dict[str, deque] = {
            name: deque(maxlen=200) for name in ("amf", "smf", "udm", "ausf")}

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        if not self.cfg.enabled or self._closed:
            return
        self._t = float(ctx.t)
        for nf in self.nfs.values():
            nf.tick(ctx)                    # 排空服务能力、更新过载指示
        self._sample_errors(self._t)
        self.bus.dispatch(ctx.t, self.nfs, ctx)
        uu = ctx.extras.get("layers", {}).get("uu")
        attached = dict(getattr(uu, "attached", {}) or {})
        for ue_id, gnb_id in sorted(attached.items()):
            old = self._known.get(ue_id)
            if old is None:
                self.amf.start_registration(ctx, ue_id, gnb_id)
                self._known[ue_id] = gnb_id
            elif old != gnb_id:
                self.amf.start_handover(ctx, ue_id, old, gnb_id)
                self._known[ue_id] = gnb_id
        for ue_id in list(self._known):
            if ue_id not in attached:
                self.amf.release(ctx, ue_id)
                self._known.pop(ue_id, None)
        self.bus.dispatch(ctx.t, self.nfs, ctx)
        if self.deferred_purge:
            # 等总线里的在途消息都落地，再清攻击注入的合成 UE/会话
            for prefix, until in list(self.deferred_purge.items()):
                self.purge_synthetic(ctx, prefix)
                if ctx.t >= until:
                    self.deferred_purge.pop(prefix, None)
        ctx.extras["core"] = self

    # ------------------------------------------------------------------
    def _sample_errors(self, now: float) -> None:
        for name in self._err_win:
            nf = getattr(self, name, None)
            if nf is None:
                continue
            recv = int(getattr(nf, "received", 0))
            rej = int(getattr(nf, "rejected", 0))
            prev = self._err_prev.get(name)
            self._err_prev[name] = (recv, rej)
            if prev is None:
                continue
            win = self._err_win[name]
            win.append((now, max(0, recv - prev[0]), max(0, rej - prev[1])))
            while win and win[0][0] < now - 2.0:
                win.popleft()

    def _error_rate(self, name: str) -> float:
        """最近 2 s 的拒绝占比（无流量时为 0）。"""
        win = self._err_win.get(name)
        if not win:
            return 0.0
        recv = sum(r for _, r, _ in win)
        rej = sum(j for _, _, j in win)
        return rej / recv if recv > 0 else 0.0

    def reattach(self, ctx, ue_id: str) -> bool:
        """让 UE 重新走一遍注册流程（攻击者伪造注销后的重入）。

        真实路径：AMF 释放 → 下一 tick 的 attach 循环重新 `start_registration`
        → 拥塞时被 AMF 队列丢弃并返回 `registration_reject`。攻击者不伪造拒绝记录。
        """
        if ue_id not in self._known:
            return False
        self.amf.release(ctx, ue_id)
        self._known.pop(ue_id, None)
        self.smf.sessions.pop(ue_id, None)
        return True

    def schedule_purge(self, prefix: str, until: float) -> None:
        """登记“在 until 之前持续清理某前缀的合成 UE”（总线里可能还有在途消息）。"""
        self.deferred_purge[prefix] = max(self.deferred_purge.get(prefix, 0.0), float(until))

    def purge_synthetic(self, ctx, prefix: str = "bot-") -> int:
        """清理攻击注入的合成 UE（AMF/UDM/SMF 三处），避免长会话累积。"""
        n = self.amf.purge(ctx, prefix)
        for ue_id in [u for u in self.smf.sessions if u.startswith(prefix)]:
            self.smf.sessions.pop(ue_id, None)
            self.smf.degraded.discard(ue_id)
        return n

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        enabled = self.cfg.enabled
        amf = self.amf.snapshot()
        smf = self.smf.snapshot()
        upf = self.upf.snapshot()
        nef = self.nef.snapshot()
        udm = self.udm.snapshot()
        ausf = self.ausf.snapshot()

        amf_load = max(
            float(amf.get("load_hint", 0.0)),
            float(amf.get("queue_depth", 0)) / max(1, self.amf.queue_capacity),
        )
        amf_err = self._error_rate("amf")
        amf_health = compute_health(
            enabled=enabled, load=amf_load, error_rate=amf_err,
            anomaly=bool(self.amf.overloaded),
            reasons=(["overloaded"] if self.amf.overloaded else []),
            metrics={"queue_depth": amf.get("queue_depth", 0)},
        )

        smf_load = smf.get("degraded_sessions", 0) / max(1, smf.get("sessions", 0))
        smf_err = self._error_rate("smf")
        smf_health = compute_health(
            enabled=enabled, load=smf_load, error_rate=smf_err,
            warning=smf.get("degraded_sessions", 0) > 0,
            reasons=([f"degraded QoS {smf['degraded_sessions']}"]
                     if smf.get("degraded_sessions") else []),
        )

        allowed_dns = set(getattr(self.cfg, "allowed_dns", ["dn:DN-01"]))
        route_ov = upf.get("route_override")
        bad_route = bool(route_ov) and route_ov not in allowed_dns
        upf_load = smf.get("degraded_sessions", 0) / max(1, upf.get("flows", 0))
        upf_health = compute_health(
            enabled=enabled, load=upf_load,
            anomaly=bad_route,
            warning=smf.get("degraded_sessions", 0) > 0,
            reasons=([f"route override {route_ov}"] if bad_route else []),
            metrics={"route": upf.get("route")},
        )

        # NEF：看“最近 2 s 是否有人退订/吊销”，累计计数只用于统计
        nef_recent = [
            (label, at) for label, at in (
                ("unsubscribe", self.nef.last_unsub_t),
                ("token revoke", self.nef.last_revoke_t),
            ) if at is not None and self._t - at <= 2.0
        ]
        nef_health = compute_health(
            enabled=enabled,
            anomaly=bool(nef_recent),
            reasons=[f"recent {label}" for label, _ in nef_recent],
        )
        udm_err = self._error_rate("udm")
        udm_health = compute_health(enabled=enabled, error_rate=udm_err,
                                    load=udm.get("subscribers", 0) / 1000.0)
        ausf_err = self._error_rate("ausf")
        ausf_health = compute_health(enabled=enabled, error_rate=ausf_err)

        dn_health = compute_health(
            enabled=enabled, anomaly=bad_route,
            reasons=["route hijack"] if bad_route else [],
        )
        gnb_health = compute_health(
            enabled=enabled,
            load=len(self._known) / 120.0,
            reasons=[f"attached {len(self._known)}"] if self._known else [],
        )

        return {
            "enabled": enabled,
            "attached": len(self._known),
            "registered": len(self.amf.ues),
            "sessions": len(self.smf.sessions),
            "flows": len(self.upf.flows),
            "bus_pending": self.bus.pending(),
            "amf": attach_health(amf, amf_health),
            "smf": attach_health(smf, smf_health),
            "upf": attach_health(upf, upf_health),
            "nef": attach_health(nef, nef_health),
            "udm": attach_health(udm, udm_health),
            "ausf": attach_health(ausf, ausf_health),
            "dn": {"entity": "dn:DN-01", "route": upf.get("route"),
                   "health": dn_health},
            "gnb": {"entity": "gnb:GNB-01", "attached": len(self._known),
                    "health": gnb_health},
        }

    def graph_nodes(self) -> list[dict]:
        return [
            {"id": self.amf.entity, "layer": "core", "kind": "amf"},
            {"id": self.smf.entity, "layer": "core", "kind": "smf"},
            {"id": self.upf.entity, "layer": "core", "kind": "upf"},
            {"id": self.udm.entity, "layer": "core", "kind": "udm"},
            {"id": self.ausf.entity, "layer": "core", "kind": "ausf"},
            {"id": self.nef.entity, "layer": "core", "kind": "nef"},
            {"id": "dn:DN-01", "layer": "core", "kind": "dn"},
        ]

    def graph_edges(self) -> list[dict]:
        edges = [
            {"src": self.amf.entity, "dst": self.smf.entity, "kind": "n11", "latency_ms": 1.5},
            {"src": self.smf.entity, "dst": self.upf.entity, "kind": "n4", "latency_ms": 1.0},
            {"src": self.upf.entity, "dst": "dn:DN-01", "kind": "n6", "latency_ms": 3.0},
            {"src": self.nef.entity, "dst": self.udm.entity, "kind": "n8", "latency_ms": 2.0},
        ]
        for s in self.scene.sensors:
            if s.kind == "gnb":
                edges.insert(0, {"src": f"gnb:{s.id}", "dst": self.amf.entity,
                                 "kind": "n2", "latency_ms": 2.0})
        return edges

    # ------------------------------------------------------------------
    def defense_rate_limit(self, ctx, limit_tps: float = 2000.0) -> dict:
        old = self.amf.capacity
        self.amf.capacity = float(limit_tps)
        self.amf.overloaded = False
        return emit_defense(ctx, layer="core", action="amf_rate_limit",
                            target=self.amf.entity, params={
                                "limit_tps": limit_tps, "old_capacity": old,
                            })

    def defense_release_session(self, ctx, ue_id: str) -> dict:
        self.amf.release(ctx, ue_id)
        return emit_defense(ctx, layer="core", action="smf_release_session",
                            target=f"ue:{ue_id}", params={"ue_id": ue_id})

    def defense_restore_route(self, ctx) -> dict:
        self.upf.restore_route(ctx)
        return emit_defense(ctx, layer="core", action="upf_route_restore",
                            target=self.upf.entity, params={"route": "dn:DN-01"})

    def close(self) -> None:
        self._closed = True
