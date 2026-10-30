"""Watch provider：与攻击无关的全局域采集器。

provider 只读仿真状态，向 TraceService 提供 target 与 frame；不执行攻击、
不修改 payload、不读取 truth。A1/A5 等攻击的检查点仍由后续 TraceProfile 负责。

当前最小切片接入 Core/Platform；Sensor/Fleet/PC5/Detector 仍由 TraceService
的 v1 全局 target 维护，后续 W1 再迁移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderTarget:
    """provider 注册到 watch 树的一个目标。"""

    id: str
    kind: str
    group: str
    label: str
    description: str = ""
    fields: list[dict[str, Any]] = field(default_factory=list)
    key_field: str | None = None
    order: int = 100


@dataclass
class ProviderFrame:
    """provider 在某个 tick 产生的一次帧更新。"""

    target_id: str
    values: dict[str, Any]
    summary: str = ""
    status: str = "changed"


class WatchProvider(Protocol):
    id: str

    def targets(self) -> list[ProviderTarget]:
        ...

    def poll(self, ctx) -> list[ProviderFrame]:
        ...


class BaseProvider:
    """带采样节流的 provider 基类。"""

    id = "base"
    interval_s = 0.1

    def __init__(self, sim) -> None:
        self.sim = sim
        self._last_t = -1.0

    def available(self) -> bool:
        return True

    def targets(self) -> list[ProviderTarget]:
        return []

    def poll(self, ctx) -> list[ProviderFrame]:
        return []

    def due(self) -> bool:
        return float(self.sim.t) - self._last_t >= self.interval_s - 1e-9

    def mark(self) -> None:
        self._last_t = float(self.sim.t)
