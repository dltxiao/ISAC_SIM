"""SMF：PDU 会话与 QoS flow 的语义状态机。"""
from __future__ import annotations

from ...config import SceneCfg
from .model import PDUSession, QoSProfile
from .nf import NetworkFunction


class SMF(NetworkFunction):
    def __init__(self, scene: SceneCfg, bus, amf, factory) -> None:
        super().__init__("smf:SMF-01", capacity=scene.core.smf_capacity_sps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=3.0, factory=factory)
        self.scene = scene
        self.bus = bus
        self.amf = amf
        self.sessions: dict[str, PDUSession] = {}
        self.degraded: set[str] = set()
        self._session_seq = 0
        self._ip_seq = 0

    def _alloc_ip(self) -> str:
        self._ip_seq += 1
        prefix = ".".join(self.scene.core.ip_pool.split(".")[:3])
        return f"{prefix}.{min(self._ip_seq, 254)}"

    # ------------------------------------------------------------------
    def handle(self, msg, ctx) -> None:
        if msg.msg_type == "pdu_session_establish":
            self._establish(msg, ctx)
        elif msg.msg_type == "pdu_session_release":
            self._release(msg, ctx)

    def _establish(self, msg, ctx) -> None:
        if not self.accept():
            self.rejected += 1
            ctx.emit("core.session", self.entity, f"ue:{msg.payload.get('ue_id', '')}", {
                "msg_type": "pdu_session_establish_reject",
                "ue_id": msg.payload.get("ue_id"),
                "cause": "congestion",
                "result": "reject",
                "latency_ms": self.base_latency_ms,
            }, span=msg.span)
            return
        ue_id = str(msg.payload.get("ue_id", ""))
        gnb_id = str(msg.payload.get("gnb_id", ""))
        tr = msg.span or self.factory.new_trace()
        self._session_seq += 1
        session = PDUSession(
            session_id=f"sess-{self._session_seq:06d}",
            ue_id=ue_id,
            dnn=self.scene.core.dnn,
            snssai=self.scene.core.snssai,
            upf_id="upf:UPF-01",
            ue_ip=self._alloc_ip(),
            qos_flows={1: QoSProfile(qfi=1)},
        )
        self.sessions[ue_id] = session
        ue = self.amf.ues.get(ue_id)
        if ue is not None:
            ue.pdu_session_id = session.session_id
            ue.session_active = True

        ctx.emit("core.session", f"ue:{ue_id}", self.entity, {
            "msg_type": "pdu_session_establish",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "pdu_session_id": session.session_id,
            "dnn": session.dnn,
            "snssai": session.snssai,
            "ue_ip": session.ue_ip,
            "result": "request",
            "latency_ms": self.base_latency_ms,
        }, span=tr)
        ctx.emit("core.session", self.entity, f"ue:{ue_id}", {
            "msg_type": "pdu_session_establish_accept",
            "ue_id": ue_id,
            "pdu_session_id": session.session_id,
            "ue_ip": session.ue_ip,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, span=self.factory.child(tr))
        ctx.emit("core.qos", self.entity, session.upf_id, {
            "msg_type": "qos_flow_create",
            "ue_id": ue_id,
            "pdu_session_id": session.session_id,
            "qfi": 1,
            "5qi": 79,
            "gfbr_kbps": 1000,
            "mfbr_kbps": 2000,
            "result": "accept",
            "latency_ms": 1.0,
        }, span=self.factory.child(tr))
        self.bus.send(
            src=self.entity, dst=session.upf_id, msg_type="flow_install",
            payload={"ue_id": ue_id, "session_id": session.session_id, "qfi": 1},
            due_t=ctx.t + 0.002, span=self.factory.child(tr),
        )

    def modify_qos(self, ctx, ue_id: str, *, five_qi: int,
                   gfbr_kbps: int = 100, span=None) -> bool:
        session = self.sessions.get(ue_id)
        if session is None:
            return False
        qos = session.qos_flows.get(1)
        if qos is None:
            qos = QoSProfile(qfi=1)
            session.qos_flows[1] = qos
        old = qos.five_qi
        qos.five_qi = int(five_qi)
        qos.gfbr_kbps = int(gfbr_kbps)
        if int(five_qi) <= 9:
            self.degraded.add(ue_id)
        else:
            self.degraded.discard(ue_id)
        tr = span or self.factory.new_trace()
        ctx.emit("core.qos", self.entity, session.upf_id, {
            "msg_type": "qos_flow_modify",
            "ue_id": ue_id,
            "pdu_session_id": session.session_id,
            "qfi": 1,
            "5qi": int(five_qi),
            "gfbr_kbps": int(gfbr_kbps),
            "mfbr_kbps": max(int(gfbr_kbps), 2000),
            "old_5qi": old,
            "result": "accept",
            "latency_ms": 2.0,
        }, span=tr)
        return True

    def _release(self, msg, ctx) -> None:
        ue_id = str(msg.payload.get("ue_id", ""))
        session = self.sessions.pop(ue_id, None)
        if session is None:
            return
        tr = msg.span or self.factory.new_trace()
        ue = self.amf.ues.get(ue_id)
        if ue is not None:
            ue.session_active = False
        ctx.emit("core.session", self.entity, f"ue:{ue_id}", {
            "msg_type": "pdu_session_release",
            "ue_id": ue_id,
            "pdu_session_id": session.session_id,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, span=tr)
        self.bus.send(
            src=self.entity, dst=session.upf_id, msg_type="flow_delete",
            payload={"ue_id": ue_id, "session_id": session.session_id},
            due_t=ctx.t + 0.002, span=self.factory.child(tr),
        )

    def snapshot(self) -> dict:
        out = super().snapshot()
        out.update({
            "sessions": len(self.sessions),
            "active_sessions": sum(1 for s in self.sessions.values() if s.state == "ACTIVE"),
            "degraded_sessions": len(self.degraded),
        })
        return out
