"""NF 基类：只做计数与统一 emit，不实现协议栈。"""
from __future__ import annotations

from ...core.ids import SpanFactory


class NetworkFunction:
    layer = "core"

    def __init__(self, name: str, *, capacity: float, queue_capacity: int = 1000,
                 base_latency_ms: float = 1.0, factory: SpanFactory | None = None) -> None:
        self.name = name
        self.capacity = float(capacity)
        self.queue_capacity = int(queue_capacity)
        self.base_latency_ms = float(base_latency_ms)
        self.factory = factory
        self.received = 0
        self.rejected = 0
        self.dropped = 0

    @property
    def entity(self) -> str:
        return self.name

    def accept(self) -> bool:
        if self.received - self.rejected >= self.queue_capacity:
            self.dropped += 1
            return False
        self.received += 1
        return True

    def handle(self, msg, ctx) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def snapshot(self) -> dict:
        return {
            "entity": self.entity,
            "received": self.received,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "capacity": self.capacity,
            "queue_capacity": self.queue_capacity,
            "base_latency_ms": self.base_latency_ms,
        }
