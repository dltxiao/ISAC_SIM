"""稳定的事件关联 ID：trace / span / parent-span。

IDs 只在一次运行内唯一，由自增计数器生成，保证同种子可复现。
不引入 UUID/随机数，避免同一场景两次运行产生不同的关联 ID。
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    chain_id: str | None = None

    def child(self, span_id: str) -> "Trace":
        return replace(self, parent_span_id=self.span_id, span_id=span_id)


class TraceFactory:
    """按顺序发号；一个 Simulation（或 CoreLayer）持有一个实例。"""

    def __init__(self, trace_prefix: str = "tr", span_prefix: str = "sp") -> None:
        self.trace_prefix = trace_prefix
        self.span_prefix = span_prefix
        self._trace_seq = 0
        self._span_seq = 0

    def _span(self) -> str:
        self._span_seq += 1
        return f"{self.span_prefix}-{self._span_seq:06d}"

    def new_trace(self, chain_id: str | None = None) -> Trace:
        self._trace_seq += 1
        return Trace(
            trace_id=f"{self.trace_prefix}-{self._trace_seq:06d}",
            span_id=self._span(),
            chain_id=chain_id,
        )

    def child(self, parent: Trace) -> Trace:
        return parent.child(self._span())
