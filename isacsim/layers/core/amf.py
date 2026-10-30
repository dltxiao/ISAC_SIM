"""AMF：注册、鉴权、PDU 会话触发、切换的语义状态机。"""
from __future__ import annotations

from ...config import SceneCfg
from .model import UEContext
from .nf import NetworkFunction


class AMF(NetworkFunction):
    def __init__(self, scene: SceneCfg, bus, ausf, factory) -> None:
        super().__init__("amf:AMF-01", capacity=scene.core.amf_capacity_tps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=2.0, factory=factory)
        self.scene = scene
        self.bus = bus
        self.ausf = ausf
        self.ues: dict[str, UEContext] = {}
        self.handovers = 0
        self.overloaded = False
        self.load_hint = 0.0
        self.queue_depth = 0
        self._ue_seq = 0

    def set_overload(self, ctx, flag: bool, *, span=None) -> None:
        self.overloaded = bool(flag)
        self.load_hint = 0.85 if flag else 0.0
        self.queue_depth = int(self.queue_capacity * 0.85) if flag else 0
        ctx.emit("core.nas", self.entity, self.entity, {
            "msg_type": "amf_overload",
            "result": "enter" if flag else "clear",
            "cause": "signaling_storm" if flag else None,
            "latency_ms": self.base_latency_ms,
        }, span=span)

    # ------------------------------------------------------------------
    def start_registration(self, ctx, ue_id: str, gnb_id: str) -> None:
        tr = self.factory.new_trace()
        if self.overloaded:
            self.rejected += 1
            ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
                "msg_type": "registration_reject",
                "ue_id": ue_id,
                "gnb_id": gnb_id,
                "cause": "congestion",
                "result": "reject",
                "latency_ms": self.base_latency_ms,
            }, span=tr)
            return
        ctx.emit("core.nas", f"ue:{ue_id}", self.entity, {
            "msg_type": "registration_request",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "snssai": self.scene.core.snssai,
            "result": "received",
            "latency_ms": self.base_latency_ms,
        }, span=tr)
        self.bus.send(
            src=f"ue:{ue_id}", dst=self.entity, msg_type="registration_request",
            payload={"ue_id": ue_id, "gnb_id": gnb_id},
            due_t=ctx.t + 0.003, span=self.factory.child(tr),
        )

    def start_handover(self, ctx, ue_id: str, from_gnb: str, to_gnb: str) -> None:
        ue = self.ues.get(ue_id)
        if ue is None:
            return
        ue.gnb_id = to_gnb
        self.handovers += 1
        tr = self.factory.new_trace()
        ctx.emit("core.handover", f"gnb:{from_gnb}", self.entity, {
            "msg_type": "handover",
            "ue_id": ue_id,
            "source_gnb": from_gnb,
            "target_gnb": to_gnb,
            "amf_ue_ngap_id": ue.amf_ue_ngap_id,
            "result": "accept",
            "interruption_ms": 12.0,
        }, span=tr)

    def release(self, ctx, ue_id: str) -> None:
        ue = self.ues.pop(ue_id, None)
        if ue is None:
            return
        tr = self.factory.new_trace()
        ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
            "msg_type": "deregistration",
            "ue_id": ue_id,
            "gnb_id": ue.gnb_id,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, span=tr)
        self.bus.send(
            src=self.entity, dst="smf:SMF-01", msg_type="pdu_session_release",
            payload={"ue_id": ue_id}, due_t=ctx.t + 0.003,
            span=self.factory.child(tr),
        )

    # ------------------------------------------------------------------
    def handle(self, msg, ctx) -> None:
        if msg.msg_type != "registration_request":
            return
        if not self.accept():
            self.rejected += 1
            ctx.emit("core.nas", self.entity, f"ue:{msg.payload.get('ue_id', '')}", {
                "msg_type": "registration_reject",
                "ue_id": msg.payload.get("ue_id"),
                "cause": "congestion",
                "result": "reject",
                "latency_ms": self.base_latency_ms,
                "queue_depth": self.queue_capacity,
            }, span=msg.span)
            return

        ue_id = str(msg.payload.get("ue_id", ""))
        gnb_id = str(msg.payload.get("gnb_id", ""))
        tr = msg.span or self.factory.new_trace()
        auth = self.ausf.authenticate(ue_id)
        auth_tr = self.factory.child(tr)
        ctx.emit("core.nas", self.entity, self.ausf.entity, {
            "msg_type": "auth_request",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "vector_id": auth.vector_id,
            "result": "request",
            "latency_ms": auth.latency_ms,
        }, span=auth_tr)
        ctx.emit("core.nas", self.ausf.entity, self.entity, {
            "msg_type": "auth_response",
            "ue_id": ue_id,
            "auth_status": "passed" if auth.ok else "failed",
            "result": "accept" if auth.ok else "reject",
            "cause": auth.cause,
            "latency_ms": auth.latency_ms,
        }, span=self.factory.child(auth_tr))

        if not auth.ok:
            self.rejected += 1
            ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
                "msg_type": "registration_reject",
                "ue_id": ue_id,
                "cause": auth.cause or "auth_failed",
                "result": "reject",
                "latency_ms": self.base_latency_ms,
            }, span=tr)
            return

        self._ue_seq += 1
        ue = UEContext(
            ue_id=ue_id,
            supi=self.ausf.udm.ensure(ue_id).supi,
            gnb_id=gnb_id,
            amf_ue_ngap_id=self._ue_seq,
            snssai=self.scene.core.snssai,
        )
        self.ues[ue_id] = ue
        accept_tr = self.factory.child(tr)
        ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
            "msg_type": "registration_accept",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "amf_ue_ngap_id": ue.amf_ue_ngap_id,
            "auth_status": "passed",
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, span=accept_tr)
        self.bus.send(
            src=self.entity, dst="smf:SMF-01", msg_type="pdu_session_establish",
            payload={"ue_id": ue_id, "gnb_id": gnb_id},
            due_t=ctx.t + 0.004, span=self.factory.child(accept_tr),
        )

    def snapshot(self) -> dict:
        out = super().snapshot()
        out.update({
            "registered": len(self.ues),
            "handovers": self.handovers,
            "queue_depth": self.queue_depth,
            "load_hint": round(self.load_hint, 3),
            "ues": [
                {"ue_id": u.ue_id, "gnb_id": u.gnb_id, "state": u.state,
                 "pdu_session_id": u.pdu_session_id}
                for u in sorted(self.ues.values(), key=lambda x: x.ue_id)[:40]
            ],
        })
        return out
