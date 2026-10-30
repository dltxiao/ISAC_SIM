"""运行期统计：记录量、攻击计数、告警、时延，供 Web 与 CLI 共用。"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttackCounters:
    primitive_instances: dict[str, int] = field(default_factory=dict)
    primitive_records: dict[str, int] = field(default_factory=dict)
    campaigns: dict[str, int] = field(default_factory=dict)
    campaigns_active: int = 0
    campaigns_done: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "primitive_instances": dict(self.primitive_instances),
            "primitive_records": dict(self.primitive_records),
            "primitive_total": sum(self.primitive_instances.values()),
            "campaigns": dict(self.campaigns),
            "campaign_total": sum(self.campaigns.values()),
            "campaigns_active": self.campaigns_active,
            "campaigns_done": self.campaigns_done,
        }


class RunStats:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.started_wall = time.time()
        self.stream_records: dict[str, int] = {}
        self.stream_bytes: dict[str, int] = {}
        self.records_total = 0
        self.bytes_total = 0
        self.attacks = AttackCounters()
        self.alerts: dict[str, int] = {}
        self.alert_total = 0
        self.anomaly_entities: set[str] = set()
        self.latencies_ms: deque[float] = deque(maxlen=20_000)
        self.defense_actions: deque[dict[str, Any]] = deque(maxlen=50)
        self.sim_time = 0.0
        self.vehicles = 0
        self.state = "idle"
        self.speed = 1.0
        self.notes: deque[dict[str, Any]] = deque(maxlen=200)

    # ---- 记录计数 -------------------------------------------------------
    def add_record(self, stream: str, nbytes: int) -> None:
        with self.lock:
            self.stream_records[stream] = self.stream_records.get(stream, 0) + 1
            self.stream_bytes[stream] = self.stream_bytes.get(stream, 0) + nbytes
            self.records_total += 1
            self.bytes_total += nbytes

    # ---- 攻击计数 -------------------------------------------------------
    def primitive_injected(self, kind: str, records: int = 1) -> None:
        with self.lock:
            a = self.attacks
            a.primitive_instances[kind] = a.primitive_instances.get(kind, 0) + 1
            a.primitive_records[kind] = a.primitive_records.get(kind, 0) + records

    def campaign_started(self, ctype: str) -> None:
        with self.lock:
            a = self.attacks
            a.campaigns[ctype] = a.campaigns.get(ctype, 0) + 1
            a.campaigns_active += 1

    def campaign_finished(self) -> None:
        with self.lock:
            a = self.attacks
            a.campaigns_active = max(0, a.campaigns_active - 1)
            a.campaigns_done += 1

    # ---- 告警与时延 -----------------------------------------------------
    def add_alert(self, rule: str, entity: str | None = None,
                  latency_ms: float | None = None) -> None:
        with self.lock:
            self.alerts[rule] = self.alerts.get(rule, 0) + 1
            self.alert_total += 1
            if entity:
                self.anomaly_entities.add(entity)
            if latency_ms is not None:
                self.latencies_ms.append(latency_ms)

    def latency_snapshot(self) -> dict[str, float]:
        with self.lock:
            data = sorted(self.latencies_ms)
        if not data:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "n": 0}
        def q(p: float) -> float:
            idx = min(len(data) - 1, max(0, int(round(p * (len(data) - 1)))))
            return round(data[idx], 3)
        return {"p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "n": len(data)}

    def add_defense(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.defense_actions.append(dict(payload))

    def note(self, level: str, message: str) -> None:
        with self.lock:
            self.notes.append({"t": round(self.sim_time, 3), "level": level, "msg": message})

    # ---- 快照 -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "speed": self.speed,
                "sim_time": round(self.sim_time, 3),
                "vehicles": self.vehicles,
                "records_total": self.records_total,
                "bytes_total": self.bytes_total,
                "streams": {
                    k: {"records": self.stream_records.get(k, 0),
                        "bytes": self.stream_bytes.get(k, 0)}
                    for k in sorted(self.stream_records)
                },
                "attacks": self.attacks.snapshot(),
                "alerts": dict(self.alerts),
                "alert_total": self.alert_total,
                "anomaly_entities": len(self.anomaly_entities),
                "latency_ms": self.latency_snapshot(),
                "wall_elapsed_s": round(time.time() - self.started_wall, 2),
                "notes": list(self.notes)[-20:],
                "defense_actions": list(self.defense_actions),
            }


def _leaves(sink) -> list:
    inner = getattr(sink, "sinks", None)
    if inner is None:
        return [sink]
    out: list = []
    for s in inner:
        out.extend(_leaves(s))
    return out


class CountingSink:
    """包装任意 Sink，统计每条流的记录数与落盘字节数。"""

    def __init__(self, inner, stats: RunStats) -> None:
        self.inner = inner
        self.stats = stats
        self._leaves = _leaves(inner)

    def _bytes(self) -> int:
        return sum(int(getattr(leaf, "bytes_written", 0)) for leaf in self._leaves)

    def emit(self, record) -> None:
        before = self._bytes()
        self.inner.emit(record)
        n = self._bytes() - before
        if n <= 0:  # 内存/自定义 Sink 无字节计数时，按 JSON 长度估算
            n = len(record.encode("_"))
        self.stats.add_record(record.stream, n)

    def close(self) -> None:
        self.inner.close()
