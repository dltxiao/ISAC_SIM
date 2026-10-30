"""核心网内部消息总线：按 (due_t, seq) 排序，确定性投递。"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from ...core.ids import SpanContext


@dataclass(order=True)
class Message:
    due_t: float
    seq: int
    src: str = field(compare=False)
    dst: str = field(compare=False)
    msg_type: str = field(compare=False)
    payload: dict = field(compare=False, default_factory=dict)
    span: SpanContext | None = field(compare=False, default=None)


class CoreBus:
    def __init__(self) -> None:
        self._heap: list[Message] = []
        self._seq = 0

    def send(self, *, src: str, dst: str, msg_type: str, payload: dict,
             due_t: float, span: SpanContext | None = None) -> None:
        self._seq += 1
        heapq.heappush(self._heap, Message(
            due_t=float(due_t), seq=self._seq, src=src, dst=dst,
            msg_type=msg_type, payload=payload, span=span,
        ))

    def dispatch(self, now: float, nfs: dict, ctx) -> int:
        """把 due_t <= now 的消息投递给目标 NF；返回投递条数。"""
        n = 0
        while self._heap and self._heap[0].due_t <= now + 1e-9:
            msg = heapq.heappop(self._heap)
            nf = nfs.get(msg.dst)
            if nf is not None:
                nf.handle(msg, ctx)
                n += 1
        return n

    def pending(self) -> int:
        return len(self._heap)
