"""AUSF：5G-AKA 鉴权的语义近似。

不实现真实密钥/向量；只返回鉴权结果、向量编号与内部时延。
"""
from __future__ import annotations

from ...config import SceneCfg
from .model import AuthResult
from .nf import NetworkFunction


class AUSF(NetworkFunction):
    def __init__(self, scene: SceneCfg, udm, factory=None) -> None:
        super().__init__("ausf:AUSF-01", capacity=scene.core.auth_capacity_tps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=2.0, factory=factory)
        self.scene = scene
        self.udm = udm
        self._vector_seq = 0

    def authenticate(self, ue_id: str) -> AuthResult:
        sub = self.udm.ensure(ue_id)
        if sub.barred:
            self.rejected += 1
            return AuthResult(False, "", self.base_latency_ms, "barred")
        self._vector_seq += 1
        return AuthResult(True, f"vec-{self._vector_seq:06d}",
                          self.base_latency_ms + 0.5)

    def snapshot(self) -> dict:
        out = super().snapshot()
        out["vectors_issued"] = self._vector_seq
        return out
