"""UPF：TEID/流表/QoS 的语义近似。"""
from __future__ import annotations

from ...config import SceneCfg
from .model import FlowRule
from .nf import NetworkFunction


class UPF(NetworkFunction):
    def __init__(self, scene: SceneCfg, factory) -> None:
        super().__init__("upf:UPF-01", capacity=scene.core.upf_capacity_mbps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=1.0, factory=factory)
        self.scene = scene
        self.flows: dict[str, FlowRule] = {}
        self._teid_seq = 1000
        self._flow_seq = 0
        self.route_override: str | None = None

    def set_route(self, ctx, route: str, *, trace=None) -> None:
        self.route_override = route
        for flow in self.flows.values():
            flow.route = route
        ctx.emit("core.upf", self.entity, self.entity, {
            "msg_type": "route_change",
            "route": route,
            "flows": len(self.flows),
            "result": "applied",
            "latency_ms": self.base_latency_ms,
        }, trace=trace)

    def restore_route(self, ctx, *, trace=None) -> None:
        self.set_route(ctx, "dn:DN-01", trace=trace)

    def handle(self, msg, ctx) -> None:
        if msg.msg_type == "flow_install":
            self._install(msg, ctx)
        elif msg.msg_type == "flow_delete":
            self._delete(msg, ctx)

    def _install(self, msg, ctx) -> None:
        ue_id = str(msg.payload.get("ue_id", ""))
        session_id = str(msg.payload.get("session_id", ""))
        qfi = int(msg.payload.get("qfi", 1))
        key = f"{ue_id}:{session_id}:{qfi}"
        if key in self.flows:
            return
        self._teid_seq += 1
        self._flow_seq += 1
        flow = FlowRule(
            flow_id=f"flow-{self._flow_seq:06d}",
            ue_id=ue_id,
            session_id=session_id,
            qfi=qfi,
            five_qi=79,
            teid=self._teid_seq,
            route=self.route_override or "dn:DN-01",
        )
        self.flows[key] = flow
        ctx.emit("core.upf", self.entity, self.entity, {
            "msg_type": "flow_install",
            "ue_id": ue_id,
            "pdu_session_id": session_id,
            "flow_id": flow.flow_id,
            "qfi": qfi,
            "5qi": flow.five_qi,
            "teid": flow.teid,
            "route": flow.route,
            "result": "installed",
            "latency_ms": self.base_latency_ms,
        }, trace=msg.trace)

    def _delete(self, msg, ctx) -> None:
        ue_id = str(msg.payload.get("ue_id", ""))
        session_id = str(msg.payload.get("session_id", ""))
        for key in list(self.flows):
            if key.startswith(f"{ue_id}:{session_id}:"):
                flow = self.flows.pop(key)
                ctx.emit("core.upf", self.entity, self.entity, {
                    "msg_type": "flow_delete",
                    "ue_id": ue_id,
                    "pdu_session_id": session_id,
                    "flow_id": flow.flow_id,
                    "teid": flow.teid,
                    "result": "deleted",
                    "latency_ms": self.base_latency_ms,
                }, trace=msg.trace)

    def snapshot(self) -> dict:
        out = super().snapshot()
        out.update({
            "flows": len(self.flows),
            "teids": len({f.teid for f in self.flows.values()}),
        })
        return out
