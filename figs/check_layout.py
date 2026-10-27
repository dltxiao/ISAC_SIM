"""粗查 SVG 中文字块是否互相重叠或越界（无法看图时的辅助工具）。

估算规则：CJK 字符宽 ≈ 1.0×字号，ASCII ≈ 0.56×字号，空格 0.3×字号。
支持 rotate()/translate() 变换（用于竖排标签）。
只检查 text-text 重叠；文本压图形是允许的。
"""
from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TEXT_RE = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S)


def text_width(s: str, size: float) -> float:
    total = 0.0
    for ch in s:
        if ch in " \t":
            total += 0.30
        elif ord(ch) < 128:
            total += 0.56
        else:
            total += 1.0
    return total * size


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_transform(text: str):
    """返回函数列表：每个函数把 (x, y) 变换一次。"""
    funcs = []
    for name, args in re.findall(r"(\w+)\(([^)]*)\)", text or ""):
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", args)]
        if name == "rotate" and len(nums) >= 1:
            ang = math.radians(nums[0])
            cx, cy = (nums[1], nums[2]) if len(nums) >= 3 else (0.0, 0.0)
            funcs.append(lambda p, a=ang, cx=cx, cy=cy: (
                cx + (p[0] - cx) * math.cos(a) - (p[1] - cy) * math.sin(a),
                cy + (p[0] - cx) * math.sin(a) + (p[1] - cy) * math.cos(a)))
        elif name == "translate" and nums:
            tx = nums[0]
            ty = nums[1] if len(nums) > 1 else 0.0
            funcs.append(lambda p, tx=tx, ty=ty: (p[0] + tx, p[1] + ty))
        elif name == "scale" and nums:
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            funcs.append(lambda p, sx=sx, sy=sy: (p[0] * sx, p[1] * sy))
    return funcs


def _bbox(x0, y0, x1, y1, funcs):
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    moved = []
    for p in pts:
        for fn in funcs:
            p = fn(p)
        moved.append(p)
    xs = [p[0] for p in moved]
    ys = [p[1] for p in moved]
    return min(xs), min(ys), max(xs), max(ys)


def _collect(node, funcs, out):
    tf = node.get("transform")
    local = funcs + _parse_transform(tf) if tf else funcs
    if _local(node.tag) == "text":
        content = "".join(node.itertext()).strip()
        if content:
            x = float(node.get("x", 0))
            y = float(node.get("y", 0))
            size = float(node.get("font-size", 13))
            anchor = node.get("text-anchor", "start")
            w = text_width(content, size)
            if anchor == "middle":
                x0 = x - w / 2
            elif anchor == "end":
                x0 = x - w
            else:
                x0 = x
            bx0, by0, bx1, by1 = _bbox(x0, y - size * 0.78, x0 + w, y + size * 0.27, local)
            out.append({"content": content, "x": bx0, "y": by0,
                        "w": bx1 - bx0, "h": by1 - by0, "size": size})
    for child in node:
        _collect(child, local, out)


def overlap(a, b) -> float:
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ix * iy


def check(path: Path, verbose=True) -> int:
    root = ET.parse(path).getroot()
    W = float(root.get("width", 0))
    H = float(root.get("height", 0))
    items: list[dict] = []
    _collect(root, [], items)
    problems = 0
    for it in items:
        if it["x"] < -2 or it["y"] < -2 or it["x"] + it["w"] > W + 2 or it["y"] + it["h"] > H + 2:
            problems += 1
            if verbose:
                print(f"  越界: {it['content']!r} bbox=({it['x']:.0f},{it['y']:.0f},"
                      f"{it['w']:.0f}x{it['h']:.0f}) canvas={W:.0f}x{H:.0f}")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ov = overlap(a, b)
            if ov > 0.18 * min(a["w"] * a["h"], b["w"] * b["h"]):
                problems += 1
                if verbose:
                    print(f"  文本重叠: {a['content']!r} ↔ {b['content']!r}")
    return problems


def main() -> int:
    files = sorted(Path("figs").glob("*.svg"))
    if len(sys.argv) > 1:
        files = [Path(p) for p in sys.argv[1:]]
    total = 0
    for f in files:
        n = check(f)
        print(f"{f.name}: {n} 处疑似问题")
        total += n
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
