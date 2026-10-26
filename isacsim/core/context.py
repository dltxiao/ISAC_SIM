"""仿真上下文与发射钩子。

攻击注入只发生在"发射器输出侧"：所有记录经 HookBus 后才落盘，
因此世界状态（SUMO 真值）不受影响，真值保持独立。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import T0_NS


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
    extras: dict[str, Any] = field(default_factory=dict)

    def ts_ns(self, t: float | None = None) -> int:
        return T0_NS + int(round((self.t if t is None else t) * 1e9))

    def emit(self, stream: str, source: str, entity: str,
             payload: dict[str, Any], *, t: float | None = None) -> dict[str, Any] | None:
        from .records import Record

        payload = self.hooks.apply(stream, payload, self)
        if payload is None:
            return None
        rec = Record(stream=stream, ts_ns=self.ts_ns(t), source=source,
                     entity=entity, payload=payload)
        self.sink.emit(rec)
        if self.detector is not None:
            self.detector.on_record(rec)
        return payload
