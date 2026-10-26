"""几何工具：坐标、方位、可见性、遮挡。

坐标约定：
- 世界坐标：SUMO 平面米制，x 向东、y 向北；
- 数学朝向：0° 指向 +x（东），逆时针为正；
- SUMO 车辆 angle：0° 指向正北，顺时针为正。
"""
from __future__ import annotations

import math


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def sumo_angle_to_heading(angle_deg: float) -> float:
    """SUMO angle（北=0，顺时针）→ 数学朝向（东=0，逆时针）。"""
    return (90.0 - angle_deg) % 360.0


def heading_to_vec(heading_deg: float) -> tuple[float, float]:
    rad = math.radians(heading_deg)
    return math.cos(rad), math.sin(rad)


def vehicle_heading(angle_deg: float) -> float:
    return sumo_angle_to_heading(angle_deg)


def bearing_deg(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def in_fov(bearing: float, yaw: float, fov: float) -> bool:
    return abs(wrap180(bearing - yaw)) <= fov / 2.0


def radial_velocity(vx: float, vy: float, dx: float, dy: float) -> float:
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return 0.0
    return vx * dx / d + vy * dy / d


def point_segment_distance(px: float, py: float,
                           ax: float, ay: float, bx: float, by: float) -> tuple[float, float]:
    """返回（点到线段距离, 投影参数 t∈[0,1]）。"""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    seg2 = vx * vx + vy * vy
    if seg2 < 1e-9:
        return math.hypot(wx, wy), 0.0
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg2))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy), t


def rect_corners(x: float, y: float, length: float, width: float,
                 heading_deg: float) -> list[tuple[float, float]]:
    hx, hy = heading_to_vec(heading_deg)
    px, py = -hy, hx
    hl, hw = length / 2.0, width / 2.0
    return [
        (x + hx * hl + px * hw, y + hy * hl + py * hw),
        (x + hx * hl - px * hw, y + hy * hl - py * hw),
        (x - hx * hl - px * hw, y - hy * hl - py * hw),
        (x - hx * hl + px * hw, y - hy * hl + py * hw),
    ]


def _point_in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1
            if px < xin:
                inside = not inside
    return inside


def occluded(sx: float, sy: float, tx: float, ty: float,
             occluders: list[tuple[float, float, float, float, float]],
             margin: float = 0.8) -> bool:
    """判断传感器→目标视线是否被矩形车辆遮挡。

    occluders: (x, y, length, width, heading_deg)
    """
    for ox, oy, length, width, heading in occluders:
        dist, t = point_segment_distance(ox, oy, sx, sy, tx, ty)
        if not (0.05 < t < 0.95):
            continue
        if dist <= max(length, width) / 2.0 + margin:
            # 采样确认：目标线段中心附近确实落在矩形内部
            if _point_in_polygon(ox, oy, rect_corners(ox, oy, length, width, heading)):
                return True
            if dist < width / 2.0:
                return True
    return False
