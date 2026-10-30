"""防御动作的统一记录入口。"""
from __future__ import annotations


def emit_defense(ctx, *, layer: str, action: str, target: str,
                 result: str = "applied", params: dict | None = None,
                 latency_ms: float = 1.0,
                 collateral: dict | None = None, span=None) -> dict:
    payload = {
        "layer": layer,
        "action": action,
        "target": target,
        "params": dict(params or {}),
        "result": result,
        "latency_ms": round(float(latency_ms), 3),
        "collateral": dict(collateral or {}),
    }
    ctx.emit("defense.action", target, target, payload, span=span)
    return payload
