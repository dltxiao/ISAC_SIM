"""NF 基类：容量/排队模型 + 统一计数。

排队模型（与 docs/09 §4.4 一致，参数是场景标定值，不是现网值）::

    service = capacity_per_s * dt
    queue   = max(0, queue + arrivals - service)
    丢包     = 队列超过 queue_capacity

`queue_depth` 是“已接收但尚未处理”的积压，单位是消息数；`latency_ms`
按 `base + queue / capacity` 估计排队时延。攻击不再直接改这些字段喂给观测，
而是把负载真的灌进 `start_registration()` 之类的人口。
"""
from __future__ import annotations

from ...core.ids import SpanFactory


class NetworkFunction:
    layer = "core"

    def __init__(self, name: str, *, capacity: float, queue_capacity: int = 1000,
                 base_latency_ms: float = 1.0, factory: SpanFactory | None = None,
                 shed_at: float = 0.8) -> None:
        self.name = name
        self.capacity = float(capacity)
        self.queue_capacity = int(queue_capacity)
        self.base_latency_ms = float(base_latency_ms)
        self.factory = factory
        self.received = 0
        self.rejected = 0
        self.dropped = 0
        self.queue_depth = 0.0
        # 准入控制开始卸载的队列水位（比例）；AMF 在 30% 就开始卸，比 SMF 早
        self.shed_at = max(0.05, min(1.0, float(shed_at)))
        self.overloaded = False
        self.load_hint = 0.0
        self._last_t: float | None = None
        # 外部“钉住”的负载下限（A10 这类语义剧本用；正常攻击靠真实到达量）
        self.hold_queue = 0.0

    @property
    def entity(self) -> str:
        return self.name

    # ---- 容量/排队 -------------------------------------------------------
    def tick(self, ctx) -> None:
        """每 tick 排空服务能力并更新过载指示。"""
        now = float(getattr(ctx, "t", 0.0) or 0.0)
        if self._last_t is None:
            self._last_t = now
        dt = max(0.0, now - self._last_t)
        self._last_t = now
        self.queue_depth = max(0.0, self.queue_depth - self.capacity * dt)
        # hold_queue 只抬“显示负载/时延”，不参与准入（A10 这类语义剧本用）
        shown = self.queue_depth + self.hold_queue
        self.load_hint = round(min(1.0, shown / max(1.0, self.queue_capacity)), 3)
        self.overloaded = self.queue_depth >= self.shed_at * self.queue_capacity

    def accept(self, ctx=None) -> bool:
        """人口接纳：队列满则丢弃（不增加 received 之外的状态）。"""
        self.received += 1
        if self.queue_depth + 1.0 > self.shed_at * self.queue_capacity:
            self.dropped += 1
            return False
        self.queue_depth += 1.0
        return True

    @property
    def latency_ms(self) -> float:
        """排队时延：base + queue / capacity（秒→毫秒）。"""
        if self.capacity <= 0:
            return self.base_latency_ms
        shown = self.queue_depth + self.hold_queue
        return round(self.base_latency_ms + shown / self.capacity * 1000.0, 2)

    def set_hold(self, queue: float) -> None:
        """抬高“显示负载”的下限（0 表示取消）；不改变准入判定。"""
        self.hold_queue = max(0.0, float(queue))

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
            "queue_depth": int(round(self.queue_depth)),
            "overloaded": self.overloaded,
            "latency_ms": self.latency_ms,
            "base_latency_ms": self.base_latency_ms,
        }
