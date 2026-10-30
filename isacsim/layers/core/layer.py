"""CoreLayer：把 AMF/SMF/UPF/UDM/AUSF 编排成一个语义核心网。

不依赖真实网络；所有过程在进程内通过 CoreBus 推进。
"""
from __future__ import annotations

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
        self._closed = False

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        if not self.cfg.enabled or self._closed:
            return
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
        ctx.extras["core"] = self

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
        amf_err = amf["rejected"] / max(1, amf["received"])
        amf_health = compute_health(
            enabled=enabled, load=amf_load, error_rate=amf_err,
            anomaly=bool(self.amf.overloaded),
            reasons=(["overloaded"] if self.amf.overloaded else []),
            metrics={"queue_depth": amf.get("queue_depth", 0)},
        )

        smf_load = smf.get("degraded_sessions", 0) / max(1, smf.get("sessions", 0))
        smf_err = smf["rejected"] / max(1, smf["received"])
        smf_health = compute_health(
            enabled=enabled, load=smf_load, error_rate=smf_err,
            warning=smf.get("degraded_sessions", 0) > 0,
            reasons=([f"degraded QoS {smf['degraded_sessions']}"]
                     if smf.get("degraded_sessions") else []),
        )

        upf_load = smf.get("degraded_sessions", 0) / max(1, upf.get("flows", 0))
        upf_health = compute_health(
            enabled=enabled, load=upf_load,
            anomaly=bool(upf.get("route_override")),
            warning=smf.get("degraded_sessions", 0) > 0,
            reasons=(["route override"] if upf.get("route_override") else []),
            metrics={"route": upf.get("route")},
        )

        nef_health = compute_health(
            enabled=enabled,
            anomaly=bool(nef.get("unsubscribed") or nef.get("revoked")),
            reasons=(
                [f"unsubscribed {nef['unsubscribed']}"] if nef.get("unsubscribed") else []
            ) + (
                [f"revoked {nef['revoked']}"] if nef.get("revoked") else []
            ),
        )
        udm_err = udm["rejected"] / max(1, udm["received"])
        udm_health = compute_health(enabled=enabled, error_rate=udm_err,
                                    load=udm.get("subscribers", 0) / 1000.0)
        ausf_err = ausf["rejected"] / max(1, ausf["received"])
        ausf_health = compute_health(enabled=enabled, error_rate=ausf_err)

        dn_health = compute_health(
            enabled=enabled,
            anomaly=bool(upf.get("route_override")),
            reasons=["route hijack"] if upf.get("route_override") else [],
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
