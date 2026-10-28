"""节点健康度：绿色健康 / 橙色繁忙 / 红色拥塞或异常。

阈值集中在这里，后端 snapshot 统一输出，前端只负责着色。
"""
from __future__ import annotations

from typing import Any

HEALTHY = "green"
BUSY = "orange"
CONGESTED = "red"
DISABLED = "disabled"
UNKNOWN = "unknown"

LOAD_ORANGE = 0.60
LOAD_RED = 0.85
ERROR_ORANGE = 0.01
ERROR_RED = 0.05
LATENCY_ORANGE = 0.70
LATENCY_RED = 1.00


def compute_health(*, enabled: bool = True, load: float = 0.0,
                   error_rate: float = 0.0, latency_ratio: float = 0.0,
                   anomaly: bool = False, warning: bool = False,
                   reasons: list[str] | None = None,
                   metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    reasons = list(reasons or [])
    if not enabled:
        return {
            "status": DISABLED,
            "score": 0.0,
            "reasons": reasons or ["disabled"],
            "metrics": dict(metrics or {}, load=0.0, error_rate=0.0,
                            latency_ratio=0.0, anomaly=False),
        }
    load = max(0.0, min(1.0, float(load)))
    error_rate = max(0.0, min(1.0, float(error_rate)))
    latency_ratio = max(0.0, float(latency_ratio))
    if anomaly or load >= LOAD_RED or error_rate >= ERROR_RED or latency_ratio >= LATENCY_RED:
        status = CONGESTED
    elif warning or load >= LOAD_ORANGE or error_rate >= ERROR_ORANGE or latency_ratio >= LATENCY_ORANGE:
        status = BUSY
    else:
        status = HEALTHY

    if warning:
        reasons.append("busy")
    if anomaly:
        reasons.append("anomaly")
    if load >= LOAD_ORANGE:
        reasons.append(f"load {load:.0%}")
    if error_rate >= ERROR_ORANGE:
        reasons.append(f"error {error_rate:.1%}")
    if latency_ratio >= LATENCY_ORANGE:
        reasons.append(f"latency {latency_ratio:.2f}x")
    if status == HEALTHY and not reasons:
        reasons = ["healthy"]
    score = min(1.0, max(load, error_rate / ERROR_RED,
                       latency_ratio / LATENCY_RED,
                       0.5 if warning else 0.0,
                       1.0 if anomaly else 0.0))
    return {
        "status": status,
        "score": round(score, 3),
        "reasons": reasons,
        "metrics": {
            "load": round(load, 3),
            "error_rate": round(error_rate, 4),
            "latency_ratio": round(latency_ratio, 3),
            "anomaly": bool(anomaly),
            "warning": bool(warning),
            **(metrics or {}),
        },
    }


def attach_health(snapshot: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    out = dict(snapshot)
    out["health"] = health
    return out
