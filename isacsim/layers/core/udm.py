"""UDM：签约数据与鉴权状态的语义近似。"""
from __future__ import annotations

import zlib

from ...config import SceneCfg
from .model import Subscriber
from .nf import NetworkFunction


class UDM(NetworkFunction):
    def __init__(self, scene: SceneCfg, factory=None) -> None:
        super().__init__("udm:UDM-01", capacity=scene.core.auth_capacity_tps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=1.0, factory=factory)
        self.scene = scene
        self.subscribers: dict[str, Subscriber] = {}

    def ensure(self, ue_id: str) -> Subscriber:
        sub = self.subscribers.get(ue_id)
        if sub is None:
            sub = Subscriber(
                supi=f"00101{zlib.crc32(ue_id.encode('utf-8')) % 10**10:010d}",
                dnn=self.scene.core.dnn,
                snssai=self.scene.core.snssai,
            )
            self.subscribers[ue_id] = sub
        return sub

    def snapshot(self) -> dict:
        out = super().snapshot()
        out["subscribers"] = len(self.subscribers)
        out["barred"] = sum(1 for s in self.subscribers.values() if s.barred)
        return out
