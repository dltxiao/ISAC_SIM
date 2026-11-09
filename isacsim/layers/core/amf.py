"""AMF：注册、鉴权、PDU 会话触发、切换的语义状态机。"""
from __future__ import annotations

from ...config import SceneCfg
from .model import UEContext
from .nf import NetworkFunction


class AMF(NetworkFunction):
    def __init__(self, scene: SceneCfg, bus, ausf, factory) -> None:
        super().__init__("amf:AMF-01", capacity=scene.core.amf_capacity_tps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=2.0, factory=factory, shed_at=0.3)
        self.scene = scene
        self.bus = bus
        self.ausf = ausf
        self.ues: dict[str, UEContext] = {}
        self.handovers = 0
        self._ue_seq = 0
        self._overload_announced = False
        # 真实 UE 的注册重试（合成 UE 不重试，否则攻击结束后会继续灌）
        self._pending: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        super().tick(ctx)
        attached = getattr(ctx.extras.get("layers", {}).get("uu"), "attached", {}) or {}
        for ue_id, (gnb_id, due) in list(self._pending.items()):
            if ue_id not in attached:
                self._pending.pop(ue_id, None)     # UE 已离开，不再重试
                continue
            if ctx.t + 1e-9 >= due:
                self.start_registration(ctx, ue_id, gnb_id, retry=True)
        # 过载指示由真实排队推导，而不是攻击者置位
        if self.overloaded != self._overload_announced:
            self._overload_announced = self.overloaded
            ctx.emit("core.nas", self.entity, self.entity, {
                "msg_type": "amf_overload",
                "result": "enter" if self.overloaded else "clear",
                "cause": "congestion" if self.overloaded else None,
                "queue_depth": int(round(self.queue_depth)),
                "latency_ms": self.latency_ms,
            })

    def purge(self, ctx, prefix: str) -> int:
        """清理某前缀的 UE（攻击注入的合成 UE），避免长会话里越积越多。"""
        gone = [u for u in self.ues if u.startswith(prefix)]
        for ue_id in gone:
            self.ues.pop(ue_id, None)
            self.ausf.udm.drop(ue_id)
        return len(gone)

    # ------------------------------------------------------------------
    def start_registration(self, ctx, ue_id: str, gnb_id: str, *,
                           retry: bool = True) -> None:
        tr = self.factory.new_trace()
        if retry:
            self._pending[ue_id] = (gnb_id, ctx.t + 0.5)
        ctx.emit("core.nas", f"ue:{ue_id}", self.entity, {
            "msg_type": "registration_request",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "snssai": self.scene.core.snssai,
            "result": "received",
            "latency_ms": self.latency_ms,
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
            "latency_ms": self.latency_ms,
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
        ue_id = str(msg.payload.get("ue_id", ""))
        gnb_id = str(msg.payload.get("gnb_id", ""))
        if not self.accept(ctx):
            self.rejected += 1
            ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
                "msg_type": "registration_reject",
                "ue_id": ue_id,
                "cause": "congestion",
                "result": "reject",
                "latency_ms": self.latency_ms,
                "queue_depth": int(round(self.queue_depth)),
            }, span=msg.span)
            if ue_id in self._pending:      # 只有原本要重试的真实 UE 才排下一次
                self._pending[ue_id] = (gnb_id, ctx.t + 0.5)
            return

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
                "latency_ms": self.latency_ms,
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
        self._pending.pop(ue_id, None)
        accept_tr = self.factory.child(tr)
        ctx.emit("core.nas", self.entity, f"ue:{ue_id}", {
            "msg_type": "registration_accept",
            "ue_id": ue_id,
            "gnb_id": gnb_id,
            "amf_ue_ngap_id": ue.amf_ue_ngap_id,
            "auth_status": "passed",
            "result": "accept",
            "latency_ms": self.latency_ms,
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
            "queue_depth": int(round(self.queue_depth)),
            "load_hint": round(self.load_hint, 3),
            "ues": [
                {"ue_id": u.ue_id, "gnb_id": u.gnb_id, "state": u.state,
                 "pdu_session_id": u.pdu_session_id}
                for u in sorted(self.ues.values(), key=lambda x: x.ue_id)[:40]
            ],
        })
        return out
