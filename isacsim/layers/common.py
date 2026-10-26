"""层间共用的小工具：装备判定、服务传感器选择。"""
from __future__ import annotations

import zlib
from typing import Any


def _ratio_of(vid: str) -> float:
    return (zlib.crc32(vid.encode("utf-8")) % 1000) / 1000.0


def is_equipped(vid: str, ratio: float) -> bool:
    return _ratio_of(vid) < ratio


def has_adas(vid: str, ratio: float) -> bool:
    return _ratio_of(vid + ":adas") < ratio


def serving_sensor(veh, sensors: list[Any]):
    """选择覆盖该车且最近的传感器。"""
    best = None
    best_d = 1e18
    for s in sensors:
        d = ((veh.x - s.x) ** 2 + (veh.y - s.y) ** 2) ** 0.5
        if d <= s.range_m and d < best_d:
            best, best_d = s, d
    return best
