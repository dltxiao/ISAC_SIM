"""仿真上下文与发射钩子。

攻击注入只发生在"发射器输出侧"：所有记录经 HookBus 后才落盘，
因此世界状态（SUMO 真值）不受影响，真值保持独立。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import T0_NS
from .ids import SpanContext


class HookBus:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = defaultdict(list)

    def on(self, stream: str, fn: Callable) -> None:
        self._hooks[stream].append(fn)

    def off(self, stream: str, fn: Callable) -> None:
        try:
            self._hooks[stream].remove(fn)
        except ValueError:
            pass

    def clear(self) -> None:
        self._hooks.clear()

    def apply(self, stream: str, payload: dict[str, Any], ctx: "SimContext") -> dict[str, Any] | None:
        """返回修改后的 payload；返回 None 表示丢弃该记录。"""
        for fn in list(self._hooks.get(stream, ())):
            payload = fn(payload, ctx)
            if payload is None:
                return None
        return payload


@dataclass
class SimContext:
    t: float
    tick: int
    step_s: float
    run_id: str
    scene: Any
    sink: Any
    stats: Any
    hooks: HookBus
    vehicles: dict[str, Any] = field(default_factory=dict)
    sumo: Any = None
    truth: Any = None
    detector: Any = None
    # 观测追踪（/trace）：TraceService；不要与分布式链路 span 混淆。
    trace: Any = None
    # 分布式链路上下文（record.trace_id/span_id 的来源）。
    span: SpanContext | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def ts_ns(self, t: float | None = None) -> int:
        return T0_NS + int(round((self.t if t is None else t) * 1e9))

    def emit(self, stream: str, source: str, entity: str,
             payload: dict[str, Any], *, t: float | None = None,
             span: SpanContext | None = None) -> dict[str, Any] | None:
        from .records import Record

        payload = self.hooks.apply(stream, payload, self)
        if payload is None:
            return None
        tr = span if span is not None else self.span
        rec = Record(
            stream=stream, ts_ns=self.ts_ns(t), source=source,
            entity=entity, payload=payload,
            trace_id=tr.trace_id if tr else None,
            span_id=tr.span_id if tr else None,
            parent_span_id=tr.parent_span_id if tr else None,
            chain_id=tr.chain_id if tr else None,
        )
        self.sink.emit(rec)
        if self.detector is not None:
            self.detector.on_record(rec)
        if self.trace is not None:
            self.trace.on_record(rec)
        if stream == "defense.action" and self.stats is not None:
            self.stats.add_defense(payload)
        return payload
