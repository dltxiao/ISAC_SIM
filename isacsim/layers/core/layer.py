"""CoreLayer：把 AMF/SMF/UPF/UDM/AUSF 编排成一个语义核心网。

不依赖真实网络；所有过程在进程内通过 CoreBus 推进。
"""
from __future__ import annotations

from ...config import SceneCfg
from ...core.ids import TraceFactory
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
        self.factory = TraceFactory()
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
        return {
            "enabled": self.cfg.enabled,
            "attached": len(self._known),
            "registered": len(self.amf.ues),
            "sessions": len(self.smf.sessions),
            "flows": len(self.upf.flows),
            "bus_pending": self.bus.pending(),
            "amf": self.amf.snapshot(),
            "smf": self.smf.snapshot(),
            "upf": self.upf.snapshot(),
            "udm": self.udm.snapshot(),
            "ausf": self.ausf.snapshot(),
            "nef": self.nef.snapshot(),
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

    def close(self) -> None:
        self._closed = True
