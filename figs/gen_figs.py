"""生成 docs/07 的示意图（SVG + PNG）。

用法：
    .venv/bin/python figs/gen_figs.py            # 生成 SVG，并用 Chromium 渲染 PNG
    .venv/bin/python figs/gen_figs.py --svg-only # 只生成 SVG

PNG 渲染需要中文字体：默认读取 figs/.cache/NotoSansSC.otf，
缺失时自动从 Noto CJK 仓库下载（约 16 MB，不进版本库）。
"""
from __future__ import annotations

import html
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
FONT_URL = ("https://raw.githubusercontent.com/googlefonts/noto-cjk/main/"
            "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf")
FONT = CACHE / "NotoSansSC.otf"

INK = "#111827"
MUTED = "#6b7280"
LINE = "#d1d5db"
PANEL = "#f8fafc"
SENSING = "#059669"
RADAR = "#0d9488"
V2X = "#2563eb"
AUTH = "#7c3aed"
CONTROL = "#d97706"
TASK = "#0891b2"
ATTACK = "#dc2626"
TRUTH = "#6b7280"

FONT_FAMILY = ('"NotoSC","Noto Sans CJK SC","Source Han Sans SC",'
               '"Microsoft YaHei","PingFang SC",sans-serif')


# --------------------------------------------------------------------------- #
class SVG:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.body: list[str] = []
        self.defs: list[str] = []

    # ---- primitives ---------------------------------------------------
    def add(self, s: str) -> None:
        self.body.append(s)

    def rect(self, x, y, w, h, fill="#ffffff", stroke=None, sw=1.2, rx=0,
             dash=None, opacity=None) -> None:
        attrs = [f'x="{x}"', f'y="{y}"', f'width="{w}"', f'height="{h}"',
                 f'fill="{fill}"']
        if rx:
            attrs.append(f'rx="{rx}"')
        if stroke:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'fill-opacity="{opacity}"')
        self.add(f'<rect {" ".join(attrs)}/>')

    def text(self, x, y, s, size=13, fill=INK, anchor="start",
             weight="normal", opacity=None, mono=False) -> None:
        fam = 'font-family="ui-monospace, SFMono-Regular, Menlo, monospace"' if mono else ""
        op = f' opacity="{opacity}"' if opacity is not None else ""
        safe = html.escape(str(s), quote=False)
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
                 f'text-anchor="{anchor}" font-weight="{weight}"{op} {fam} '
                 f'dominant-baseline="middle">{safe}</text>')

    def text_rot(self, x, y, text, angle=-90, size=11, fill=MUTED,
                 anchor="middle", weight="normal") -> None:
        self.add(f'<g transform="rotate({angle} {x} {y})">')
        self.text(x, y, text, size=size, fill=fill, anchor=anchor, weight=weight)
        self.add('</g>')

    def lines(self, x, y, lines, size=12, fill=INK, anchor="middle",
              leading=1.45, weight="normal") -> None:
        for i, line in enumerate(lines):
            self.text(x, y + i * size * leading, line, size=size, fill=fill,
                      anchor=anchor, weight=weight)

    def line(self, x1, y1, x2, y2, color=LINE, sw=1.2, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'stroke="{color}" stroke-width="{sw}"{d}/>')

    def arrow(self, x1, y1, x2, y2, color=MUTED, sw=1.8, dash=None, head=9) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'stroke="{color}" stroke-width="{sw}"{d}/>')
        ang = math.atan2(y2 - y1, x2 - x1)
        p1 = (x2 - head * math.cos(ang) + head * 0.55 * math.sin(ang),
              y2 - head * math.sin(ang) - head * 0.55 * math.cos(ang))
        p2 = (x2 - head * math.cos(ang) - head * 0.55 * math.sin(ang),
              y2 - head * math.sin(ang) + head * 0.55 * math.cos(ang))
        self.add(f'<polygon points="{x2},{y2} {p1[0]:.1f},{p1[1]:.1f} '
                 f'{p2[0]:.1f},{p2[1]:.1f}" fill="{color}"/>')

    def circle(self, cx, cy, r, fill="none", stroke=None, sw=1.2, dash=None,
               opacity=None) -> None:
        attrs = [f'cx="{cx}"', f'cy="{cy}"', f'r="{r}"', f'fill="{fill}"']
        if stroke:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'fill-opacity="{opacity}"')
        self.add(f'<circle {" ".join(attrs)}/>')

    def path(self, d, fill="none", stroke=None, sw=1.2, dash=None,
             opacity=None) -> None:
        attrs = [f'd="{d}"', f'fill="{fill}"']
        if stroke:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'fill-opacity="{opacity}"')
        self.add(f'<path {" ".join(attrs)}/>')

    def clip(self, cid, x, y, w, h) -> None:
        self.defs.append(f'<clipPath id="{cid}"><rect x="{x}" y="{y}" '
                         f'width="{w}" height="{h}"/></clipPath>')

    # ---- composites ----------------------------------------------------
    def card(self, x, y, w, h, title, sub=(), color=SENSING, tag=None,
             dashed=False, title_size=14, sub_size=11) -> None:
        self.rect(x, y, w, h, fill=color, opacity=0.07, rx=10)
        self.rect(x, y, w, h, fill="none", stroke=color, sw=1.6, rx=10,
                  dash="6 4" if dashed else None)
        self.rect(x, y, w, 4, fill=color, rx=2)
        start = y + 26
        self.lines(x + w / 2, start, [title], size=title_size, weight="bold",
                   anchor="middle")
        if sub:
            self.lines(x + w / 2, start + title_size * 1.5 + 8, sub,
                       size=sub_size, fill=MUTED, anchor="middle", leading=1.5)
        if tag:
            tw = 10 + len(tag) * 11
            self.rect(x + w - tw, y - 26, tw, 20, fill=ATTACK, rx=10)
            self.text(x + w - tw / 2, y - 16, tag, size=10.5, fill="#ffffff",
                      anchor="middle", weight="bold")

    def chip(self, x, y, label, color=MUTED, size=11):
        w = 18 + len(label) * (size * 0.98 if any("\u4e00" <= ch <= "\u9fff" for ch in label) else size * 0.62)
        self.rect(x, y, w, 24, fill=color, opacity=0.10, rx=12)
        self.rect(x, y, w, 24, fill="none", stroke=color, sw=1, rx=12)
        self.text(x + w / 2, y + 12, label, size=size, fill=color, anchor="middle")
        return w

    # ---- output --------------------------------------------------------
    def svg(self) -> str:
        defs = f'<defs>{"".join(self.defs)}</defs>' if self.defs else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
                f'height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                f'<style>text{{font-family:{FONT_FAMILY};}}</style>'
                f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>'
                f'{defs}{"".join(self.body)}</svg>')

    def save(self, name: str) -> Path:
        p = ROOT / name
        p.write_text(self.svg(), encoding="utf-8")
        return p


# --------------------------------------------------------------------------- #
# 图 1：场景
# --------------------------------------------------------------------------- #
def fig_scene() -> SVG:
    s = SVG(1240, 690)
    X0, SCALE, CY = 70, 2.55, 345
    Y0 = 118

    def px(xm): return X0 + xm * SCALE
    def py(ym): return CY - ym * SCALE

    s.text(24, 34, "图 1  模拟场景：400 m 三车道城市干道 + 1 个信号路口", size=20, weight="bold")
    s.text(24, 60, "SUMO 提供唯一世界状态；3 个 RSU 覆盖两两重叠；1 个通感基站（gNB）；"
                   "东西双向各 3 车道，南北支路各 2 车道", size=12.5, fill=MUTED)

    # 地图底
    s.clip("map", X0, 118, 1020, 428)
    s.rect(X0, 118, 1020, 428, fill="#f8fafc", stroke="#e5e7eb", sw=1)
    for xm in range(0, 401, 50):
        s.line(px(xm), 118, px(xm), 546, color="#eef2f7", sw=1)
        s.text(px(xm), 560, str(xm), size=9.5, fill="#9ca3af", anchor="middle")
    s.text(px(200), 578, "x（m，东）", size=9.5, fill="#9ca3af", anchor="middle")

    # 道路
    s.rect(px(0), py(9.6), 400 * SCALE, 19.2 * SCALE, fill="#e5e7eb")
    s.rect(px(200) - 6.4 * SCALE, 118, 12.8 * SCALE, 428, fill="#e5e7eb")
    s.rect(px(200) - 6.4 * SCALE, py(9.6), 12.8 * SCALE, 19.2 * SCALE, fill="#d1d5db")

    # 车道线
    for ym in (-9.6, -6.4, -3.2, 3.2, 6.4, 9.6):
        s.line(px(0), py(ym), px(400), py(ym), color="#ffffff", sw=1.4,
               dash="10 8" if abs(ym) not in (9.6,) else None)
    s.line(px(0), py(0), px(400), py(0), color="#f59e0b", sw=2.2)
    # 支路车道线
    for xm in (196.8, 203.2):
        s.line(px(xm), 118, px(xm), py(9.6), color="#ffffff", sw=1.2, dash="8 8")
        s.line(px(xm), py(-9.6), px(xm), 546, color="#ffffff", sw=1.2, dash="8 8")

    # 斑马线
    for i in range(6):
        y = py(-8.5) + i * 7
        s.rect(px(200) - 10 * SCALE / 2, y, 10 * SCALE, 4, fill="#ffffff", rx=1)
    for i in range(5):
        x = px(200) - 10 * SCALE / 2 + i * 7
        s.rect(x, py(9.6) - 8, 3.5, 8, fill="#ffffff", rx=1)
        s.rect(x, py(-9.6), 3.5, 8, fill="#ffffff", rx=1)

    # 信号灯（放在路口右侧，避开 RSU-02 标签）
    lx, ly = px(200) + 26, py(9.6) - 30
    s.rect(lx - 8, ly - 14, 16, 28, fill="#334155", rx=3)
    for i, c in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        s.circle(lx, ly - 8 + i * 8, 3.2, fill=c)
    s.text(lx + 14, ly - 2, "信号 C", size=10, fill="#475569")

    # RSU 覆盖
    for sid, sx, sy, yaw in (("RSU-01", 60, 6, 0), ("RSU-02", 200, 6, 0),
                             ("RSU-03", 340, 6, 180)):
        pts = []
        for k in range(25):
            a = math.radians(yaw - 60 + 120 * k / 24)
            pts.append((px(sx + 220 * math.cos(a)), py(sy + 220 * math.sin(a))))
        d = (f"M {px(sx)},{py(sy)} L " +
             " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z")
        s.add(f'<g clip-path="url(#map)"><path d="{d}" fill="#3b82f6" '
              f'fill-opacity="0.07" stroke="#3b82f6" stroke-opacity="0.35" '
              f'stroke-width="1" stroke-dasharray="7 5"/></g>')

    # gNB 覆盖示意
    for r in (60, 90, 120):
        s.add(f'<g clip-path="url(#map)"><circle cx="{px(200)}" cy="{py(-40)}" '
              f'r="{r}" fill="none" stroke="#059669" stroke-opacity="0.35" '
              f'stroke-width="1" stroke-dasharray="4 6"/></g>')

    # 静态目标
    sx, sy = px(199), py(10)
    s.add(f'<polygon points="{sx},{sy-9} {sx+8},{sy-4} {sx+7},{sy+6} '
          f'{sx-7},{sy+6} {sx-8},{sy-4}" fill="#ef4444" stroke="#991b1b"/>')
    s.text(sx, sy, "停", size=8, fill="#ffffff", anchor="middle", weight="bold")
    s.text(sx - 13, sy - 16, "真停止牌", size=9.5, fill="#7f1d1d", anchor="end")

    ox, oy = px(330), py(-5.5)
    s.add(f'<polygon points="{ox},{oy-9} {ox+9},{oy+6} {ox-9},{oy+6}" '
          f'fill="#f59e0b" stroke="#92400e"/>')
    s.text(ox, oy + 2, "障", size=8, fill="#3f1d00", anchor="middle", weight="bold")
    s.text(ox + 12, oy - 12, "障碍物", size=9.5, fill="#78350f")

    # 车辆
    def veh(xm, ym, heading, label=None, truck=False):
        cx, cy = px(xm), py(ym)
        l, w = (26, 10) if truck else (19, 8)
        s.add(f'<g transform="translate({cx},{cy}) rotate({-heading})">'
              f'<rect x="{-l/2}" y="{-w/2}" width="{l}" height="{w}" rx="2" '
              f'fill="{"#64748b" if truck else "#94a3b8"}" stroke="#475569" '
              f'stroke-width="0.8"/></g>')
        if label:
            s.text(cx + 12, cy - 14, label, size=9.5, fill="#475569")

    veh(90, -1.6, 0, "eastbound.0")
    veh(140, -4.8, 0)
    veh(300, -8.0, 0, "eastbound.7")
    veh(380, -1.6, 0)
    veh(150, 1.6, 180)
    veh(255, 4.8, 180, "westbound.2")
    veh(355, 1.6, 180)
    veh(203, -55, 90, "northbound.1")
    veh(203, 55, 270)
    veh(60, -6.4, 0, truck=True)

    # 传感器图标
    for sid, sxm, sym, kind in (("RSU-01", 60, 6, "rsu"), ("RSU-02", 200, 6, "rsu"),
                                 ("RSU-03", 340, 6, "rsu"), ("GNB-01", 200, -40, "gnb")):
        cx, cy = px(sxm), py(sym)
        color = "#2563eb" if kind == "rsu" else "#059669"
        s.line(cx, cy, cx, cy - 14, color=color, sw=2)
        s.circle(cx, cy - 17, 5, fill=color, stroke="#ffffff", sw=1.5)
        s.text(cx, cy - 36, sid, size=10.5, fill=color, anchor="middle", weight="bold")
        if kind == "rsu":
            s.text(cx, cy - 20, "RSU", size=8, fill="#ffffff", anchor="middle")

    # y 轴
    s.arrow(44, 540, 44, 150, color="#9ca3af", sw=1.4)
    s.text(24, 136, "y ↑ 北", size=10, fill="#9ca3af")

    # 图例
    lx, ly = 70, 600
    s.rect(lx, ly, 1100, 52, fill=PANEL, stroke="#e5e7eb", rx=8)
    items = [
        ("RSU（220 m / 120°）", "#2563eb", "square"),
        ("通感基站 gNB（400 m）", "#059669", "circle"),
        ("覆盖扇区（重叠区）", "#3b82f6", "sector"),
        ("真停止牌", "#ef4444", "oct"),
        ("障碍物", "#f59e0b", "tri"),
        ("车辆（尺寸示意放大）", "#94a3b8", "veh"),
    ]
    x = lx + 20
    for label, color, shape in items:
        if shape == "square":
            s.rect(x, ly + 21, 10, 10, fill=color, rx=2)
        elif shape == "circle":
            s.circle(x + 5, ly + 26, 5, fill=color)
        elif shape == "sector":
            s.path(f"M {x+5},{ly+31} L {x-3},{ly+18} A 10 10 0 0 1 {x+13},{ly+18} Z",
                   fill=color, opacity=0.25, stroke=color, sw=1)
        elif shape == "oct":
            s.add(f'<polygon points="{x+5},{ly+19} {x+10},{ly+22} {x+10},{ly+28} '
                  f'{x+5},{ly+31} {x},{ly+28} {x},{ly+22}" fill="{color}"/>')
        elif shape == "tri":
            s.add(f'<polygon points="{x+5},{ly+19} {x+11},{ly+31} {x-1},{ly+31}" fill="{color}"/>')
        else:
            s.rect(x, ly + 22, 14, 7, fill=color, rx=1)
        s.text(x + 18, ly + 26, label, size=10, fill="#475569")
        x += 24 + len(label) * 10.5

    s.text(70, 672, "坐标：ENU 米制（x 东、y 北）；车辆朝向按 SUMO angle 换算；"
                    "覆盖半径 220 m（RSU）/ 400 m（gNB，图中仅示意）；"
                    "RSU-01/02 与 RSU-02/03 覆盖重叠，是\"多源互证\"的前提。",
           size=10.5, fill=MUTED)
    return s


# --------------------------------------------------------------------------- #
# 图 2：正常态流程
# --------------------------------------------------------------------------- #
def fig_normal() -> SVG:
    s = SVG(1240, 840)
    s.text(24, 34, "图 2  正常状态下的数据流与闭环", size=20, weight="bold")
    s.text(24, 60, "一个 0.05 s 步内：SUMO 推进 → 感知/通信/接入 → ADAS → 任务/图；"
                   "观测落盘并送检测器；真值与检测隔离", size=12.5, fill=MUTED)
    s.text(24, 84, "正常态包含难负样本：跟驰换道、信号灯停车、真实急刹、大车遮挡、小区切换、"
                   "GNSS 城市峡谷退化——它们都会让单条规则\"看起来像攻击\"",
           size=11, fill="#b45309")

    # SUMO
    s.card(80, 110, 1080, 72, "SUMO（TraCI）— 唯一世界状态",
           ["车辆位姿 / 速度 / 类型 / 车道 / 信号灯；步长 0.05 s；固定 seed"],
           color="#334155", title_size=15, sub_size=11.5)

    # 三列
    cols = [
        (80, 330, SENSING, "感知层", [
            "回波 echo：每目标/帧（10–20 Hz）",
            "点迹 track：由回波派生",
            "目标列表 objects：相机等价（每帧）",
            "GNSS 10 Hz；帧号/时钟随帧输出",
        ]),
        (440, 360, V2X, "通信层", [
            "CAM 10 Hz×DCC（紧急 20 Hz）",
            "CPM 10 Hz；DENM 事件；SPaT 1 Hz",
            "CBR = 0.92·offered/1200 + jam",
            "PER/时延由 CBR 推导；丢包也落盘",
        ]),
        (830, 330, AUTH, "接入 / 认证", [
            "attach / handover（12–40 s）",
            "cert 6–12 s 轮换；pseudonym",
            "fingerprint 1 Hz：CFO/IQ/RSSI",
            "指纹由设备固有偏差决定",
        ]),
    ]
    for x, w, color, title, subs in cols:
        s.card(x, 230, w, 176, title, subs, color=color, title_size=15, sub_size=10.5)
        s.arrow(620, 182, x + w / 2, 228, color="#94a3b8", sw=1.4)
    s.text(652, 206, "几何投影 + 噪声", size=10, fill=MUTED, anchor="start")

    # 控制与任务
    s.card(80, 450, 560, 150, "控制层：ADAS（TTC / AEB）", [
        "对同车道 2–60 m 目标：TTC = 纵向距离 / 相对速度",
        "TTC < 2.5 s → 制动指令（减速度 3 m/s²，反应延迟 120 ms）",
        "执行器：sumo.slow_down() 回写 SUMO → 真实减速/排队",
        "正常场景下只在\"真有前车\"时触发",
    ], color=CONTROL, title_size=15, sub_size=10.5)
    s.card(670, 450, 490, 150, "任务层 / 图快照", [
        "task 1 Hz：航路点、阶段、偏差、时限",
        "graph 10 Hz：覆盖边、Uu 边",
        "供 STGNN 与攻击链聚合使用",
    ], color=TASK, title_size=15, sub_size=10.5)
    s.arrow(400, 406, 300, 448, color="#94a3b8", sw=1.4)
    s.text(412, 422, "消费被篡改后的目标列表", size=10, fill=MUTED, anchor="start")

    # 回写 SUMO
    s.path("M 80 525 L 46 525 L 46 146", stroke=CONTROL, sw=2, dash="6 4")
    s.arrow(46, 146, 78, 146, color=CONTROL, sw=2)
    s.text_rot(34, 336, "执行器回写 SUMO", angle=-90, size=10, fill=CONTROL,
               weight="bold")

    # 落盘 + 检测
    s.card(80, 650, 500, 66, "观测流落盘：JSONL / Parquet",
           ["18 类流；ts_ns（仿真时间）+ t_wall_ns（墙钟）"], color="#475569",
           title_size=14, sub_size=10.5)
    s.card(620, 650, 540, 66, "参考检测器（在线，只读观测流）",
           ["规则：多源互证 / 残差 / 位置跳变 / 链路 / 控制合理性"],
           color="#0f766e", title_size=14, sub_size=10.5)
    s.arrow(360, 600, 330, 648, color="#94a3b8", sw=1.4)
    s.arrow(740, 600, 890, 648, color="#94a3b8", sw=1.4)

    # 真值隔离
    s.rect(80, 748, 1080, 54, fill=TRUTH, opacity=0.05, rx=8)
    s.rect(80, 748, 1080, 54, fill="none", stroke=TRUTH, sw=1.4, rx=8, dash="7 5")
    s.text(104, 775, "真值 truth/events.jsonl（事件 / 阶段 / 参数）", size=13,
           weight="bold", fill=TRUTH)
    s.text(470, 775, "—— 仅评估与运维对账使用；检测器不可读（有静态断言）",
           size=11, fill=TRUTH)
    s.text(1080, 775, "×", size=18, fill=ATTACK, anchor="middle", weight="bold")
    s.text(1112, 775, "隔离", size=10, fill=ATTACK, anchor="middle")
    return s


# --------------------------------------------------------------------------- #
# 图 3：四类攻击
# --------------------------------------------------------------------------- #
def attack_figure(code, title, subtitle, boxes, evidence, envelope, note):
    W, H = 1240, 470
    s = SVG(W, H)
    s.text(24, 34, f"图 3-{code[-1]}  {title}", size=20, weight="bold")
    s.text(24, 60, subtitle, size=12.5, fill=MUTED)

    n = len(boxes)
    bw, gap = 166, 20
    total = n * bw + (n - 1) * gap
    x = (W - total) / 2
    y = 100
    for i, (b_title, b_sub, color, tag) in enumerate(boxes):
        s.card(x, y, bw, 126, b_title, b_sub, color=color, tag=tag,
               dashed=(tag == "注入"), title_size=13.5, sub_size=10.5)
        if i < n - 1:
            ax1 = x + bw + 2
            ax2 = x + bw + gap - 2
            s.arrow(ax1, y + 63, ax2, y + 63,
                    color=ATTACK if boxes[i + 1][3] == "注入" else "#94a3b8",
                    sw=2.2 if boxes[i + 1][3] == "注入" else 1.6)
        x += bw + gap

    # 证据
    ey = 270
    s.text(40, ey, "可观测证据", size=12.5, weight="bold", fill="#334155")
    cx = 140
    for label in evidence:
        cx += s.chip(cx, ey - 12, label, color="#1d4ed8") + 10
    s.text(40, ey + 42, "包络判据", size=12.5, weight="bold", fill="#334155")
    s.chip(140, ey + 30, envelope, color="#b45309")
    s.text(40, ey + 88, "检测规则", size=12.5, weight="bold", fill="#334155")
    s.text(140, ey + 88, note, size=11, fill=MUTED)
    return s


def fig_attack_a1():
    return attack_figure(
        "A1", "A1 感知数据篡改诱导通信拥塞",
        "攻击者 → 伪造路侧目标列表 → ADAS 误判 → 真实制动 → 通信负载上升",
        [
            ("攻击者", ["伪造 RSU 目标列表"], ATTACK, ""),
            ("RSU 目标列表", ["sensing.objects", "车前 22–58 m", "其他 RSU 看不到"], SENSING, "注入"),
            ("ADAS 判定", ["TTC < 2.5 s", "被篡改感知驱动"], CONTROL, ""),
            ("车辆制动", ["sumo.slow_down", "真实减速 / 排队"], CONTROL, ""),
            ("V2X 报文", ["DENM + CAM 升频", "10 → 20 Hz"], V2X, ""),
            ("链路指标", ["CBR↑ / PER↑", "附加负载", "150–650 msg/s"], V2X, ""),
        ],
        ["ghost_object", "cbr_spike", "link_degraded"],
        "extra_load ≥ 250 msg/s",
        "单 RSU 的静止目标无第二源佐证 → ghost_object；CBR 突增 + PER 恶化 → 链路规则。",
    )


def fig_attack_a2():
    return attack_figure(
        "A2", "A2 物理环境欺骗 + 控制指令误导",
        "攻击者 → 假停止牌 + 注入制动 + 链路干扰 → 急刹 / 纠正信息被拖慢",
        [
            ("攻击者", ["伪造停止牌", "车前 28 m"], ATTACK, ""),
            ("目标列表", ["stop_sign", "地图中不存在"], SENSING, "注入"),
            ("控制指令", ["inject_brake", "3.0–5.5 m/s²，ttc=99"], CONTROL, "注入"),
            ("车辆响应", ["急刹", "任务偏差"], CONTROL, ""),
            ("链路干扰", ["jam 0.05–0.4", "PER / 时延上升"], V2X, "注入"),
            ("纠正受阻", ["CPM 丢失 / 延迟", "真实停止牌信息到不了"], V2X, ""),
        ],
        ["ghost_object", "brake_without_hazard", "link_degraded"],
        "jam ≥ 0.05",
        "停止牌与地图/多源不一致 → ghost_object；TTC=99 的制动 → 控制合理性规则。",
    )


def fig_attack_a3():
    return attack_figure(
        "A3", "A3 跨层隐蔽攻击（雷达回波渐偏）",
        "攻击者 → 回波距离线性渐偏 → 点迹同步偏移 → 目标列表仍为真值 → 多帧残差",
        [
            ("攻击者", ["回波距离渐偏", "0.8–3.0 m / duration"], ATTACK, ""),
            ("雷达回波", ["range += bias·t/T", "所有传感器"], RADAR, "注入"),
            ("点迹", ["由被篡改回波派生", "range 同步偏移"], RADAR, ""),
            ("目标列表", ["仍为真值", "三层出现稳定残差"], SENSING, ""),
            ("控制", ["60% 时小指令", "1.2 m/s²，掩盖意图"], CONTROL, ""),
            ("遥测 / 链路", ["保持正常", "单帧低于门限"], V2X, ""),
        ],
        ["radar_range_bias", "brake_without_hazard"],
        "bias ≥ 1.0 m",
        "仅雷达规则：幅度 0.15 检出 17.5%，0.30 为 70%，≥0.50 为 100%；控制规则全幅度命中。",
    )


def fig_attack_a4():
    return attack_figure(
        "A4", "A4 双因素仿冒攻击",
        "攻击者 → 克隆伪名 + 复制信道指纹 → 以受害者身份发消息 → 位置/定位矛盾",
        [
            ("攻击者", ["克隆双因素", "伪名 + 指纹"], ATTACK, ""),
            ("auth.cert", ["clone 事件", "与受害者同伪名"], AUTH, "注入"),
            ("auth.fingerprint", ["复制指纹", "CFO/IQ/RSSI 一致"], AUTH, "注入"),
            ("v2x.cam", ["伪名发消息", "位置 +45 m / +8 m"], V2X, "注入"),
            ("位置一致性", ["1 s 内跳变 45 m", "同一伪名两处"], SENSING, ""),
            ("认证 / 定位", ["CAM vs GNSS 不符", "> 12 m"], SENSING, ""),
        ],
        ["cam_position_jump", "cam_gnss_mismatch"],
        "spoof_rate ≥ 2 Hz",
        "同一伪名 1 s 内 >25 m 跳变 → 位置规则；CAM 与 GNSS 相差 >12 m → 双源规则。",
    )


# --------------------------------------------------------------------------- #
# 图 4：跨层关联
# --------------------------------------------------------------------------- #
def fig_crosslayer() -> SVG:
    s = SVG(1240, 740)
    s.text(24, 34, "图 4  跨层攻击的关联方式与检测映射", size=20, weight="bold")
    s.text(24, 60, "同一实体在多域有观测 → 攻击制造跨域矛盾 → 检测比较同一实体的不同域，"
                   "而不是单点阈值", size=12.5, fill=MUTED)

    # ---- 矩阵 ----
    rows = ["雷达回波", "感知目标", "V2X 报文", "链路指标", "认证 / 接入", "控制 / 任务"]
    cols = ["A1", "A2", "A3", "A4"]
    x0, y0 = 130, 110
    cw, rh = 120, 56
    s.text(x0 + cw * 2, y0 - 16, "攻击类型 × 观测域", size=13, weight="bold",
           anchor="middle", fill="#334155")
    for j, c in enumerate(cols):
        s.text(x0 + cw * j + cw / 2, y0 + 16, c, size=13, weight="bold",
               anchor="middle")
    for i, r in enumerate(rows):
        cy = y0 + 36 + i * rh
        s.text(x0 - 12, cy + rh / 2, r, size=12, anchor="end", fill="#334155")
        s.line(x0 - 4, cy + rh, x0 + cw * 4, cy + rh, color="#e5e7eb", sw=1)

    marks = {
        (1, 0): "in", (2, 0): "aff", (3, 0): "ev", (5, 0): "aff",
        (1, 1): "in", (3, 1): "aff", (5, 1): "in",
        (0, 2): "in", (1, 2): "ev", (5, 2): "aff",
        (2, 3): "in", (4, 3): "in",
    }

    style = {"in": ("#dc2626", "注入"), "aff": ("#d97706", "影响"),
             "ev": ("#2563eb", "证据")}
    for i in range(len(rows)):
        for j in range(len(cols)):
            cy = y0 + 36 + i * rh
            kind = marks.get((i, j))
            cx = x0 + cw * j
            s.rect(cx + 4, cy + 4, cw - 8, rh - 8, fill="#ffffff", rx=6)
            if kind:
                color, label = style[kind]
                s.rect(cx + 4, cy + 4, cw - 8, rh - 8, fill=color, opacity=0.10, rx=6)
                s.rect(cx + 4, cy + 4, cw - 8, rh - 8, fill="none", stroke=color,
                       sw=1.4 if kind == "in" else 1.0, rx=6,
                       dash="5 4" if kind == "in" else None)
                s.text(cx + cw / 2, cy + rh / 2, label, size=11, fill=color,
                       anchor="middle", weight="bold" if kind == "in" else "normal")
            s.rect(cx + 4, cy + 4, cw - 8, rh - 8, fill="none", stroke="#eef2f7", sw=0.8, rx=6)

    # 图例
    lx, ly = 130, 502
    for k, (color, label) in style.items():
        s.rect(lx, ly, 18, 18, fill=color, opacity=0.10, rx=4)
        s.rect(lx, ly, 18, 18, fill="none", stroke=color, sw=1.2, rx=4,
               dash="4 3" if k == "in" else None)
        s.text(lx + 26, ly + 10, {"in": "注入点", "aff": "受影响", "ev": "证据来源"}[k],
               size=11, fill="#475569")
        lx += 120

    # ---- 关联边 ----
    rx = 680
    s.text(rx, 100, "跨源一致性比较（关联边）", size=13, weight="bold", fill="#334155")
    pairs = [
        ("目标列表", "地图 / 多 RSU", "ghost_object"),
        ("点迹 range", "目标列表 pos", "radar_range_bias"),
        ("CAM", "GNSS", "cam_gnss_mismatch"),
        ("CAM 同伪名", "1 s 前位置", "cam_position_jump"),
        ("控制指令", "TTC / 前车", "brake_without_hazard"),
        ("CBR / PER", "申报负载", "cbr_spike / link_degraded"),
    ]
    y = 130
    for left, right, rule in pairs:
        lw, rw = 118, 132
        s.rect(rx, y, lw, 34, fill="#eef2ff", stroke="#c7d2fe", rx=8)
        s.text(rx + lw / 2, y + 17, left, size=10.5, anchor="middle", fill="#3730a3")
        s.rect(rx + 280, y, rw, 34, fill="#ecfdf5", stroke="#a7f3d0", rx=8)
        s.text(rx + 280 + rw / 2, y + 17, right, size=10.5, anchor="middle", fill="#065f46")
        s.arrow(rx + lw + 6, y + 17, rx + 276, y + 17, color="#64748b", sw=1.4, dash="4 3")
        s.arrow(rx + 276, y + 17, rx + lw + 6, y + 17, color="#64748b", sw=1.4, dash="4 3")
        s.text(rx + 140, y + 52, rule, size=10, fill="#1d4ed8", anchor="middle", mono=True)
        y += 70

    # ---- 统一范式 ----
    s.rect(80, 590, 1080, 110, fill="#f8fafc", stroke="#e5e7eb", rx=10)
    s.text(104, 620, "统一范式", size=13, weight="bold", fill="#334155")
    s.text(104, 648, "一个攻击事件 = 一个实体在至少两个域留下互相矛盾的观测；"
                     "检测 = 把这些观测对齐到同一实体/同一时间窗，再判断矛盾是否可解释。",
           size=11.5, fill="#475569")
    s.text(104, 678, "例：A3 的\"回波 vs 点迹 vs 目标列表\"、A4 的\"伪名位置 vs GNSS\"、"
                     "A2 的\"制动指令 vs 前车 TTC\"，都是同一实体两条观测的对账。",
           size=11.5, fill="#475569")
    return s


# --------------------------------------------------------------------------- #
def render_pngs(svgs: list[Path]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright，跳过 PNG 渲染")
        return
    if not FONT.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        import urllib.request

        print("下载中文字体（仅用于渲染）…")
        urllib.request.urlretrieve(FONT_URL, FONT)

    html = CACHE / "_render.html"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for svg in svgs:
            text = svg.read_text(encoding="utf-8")
            html.write_text(
                "<!doctype html><html><head><meta charset='utf-8'><style>"
                "@font-face{font-family:'NotoSC';src:url('NotoSansSC.otf') format('opentype');}"
                "html,body{margin:0;background:#fff}"
                f"</style></head><body>{text}</body></html>",
                encoding="utf-8",
            )
            w = int(text.split('width="')[1].split('"')[0])
            h = int(text.split('height="')[1].split('"')[0])
            page = browser.new_page(viewport={"width": w, "height": h},
                                    device_scale_factor=2)
            page.goto(html.resolve().as_uri())
            page.wait_for_timeout(400)
            out = svg.with_suffix(".png")
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
            page.close()
            print(f"  {out.name}  {w}×{h}px")
        browser.close()


def main() -> int:
    figs = {
        "fig-01-scene.svg": fig_scene(),
        "fig-02-normal-flow.svg": fig_normal(),
        "fig-03a-attack-a1.svg": fig_attack_a1(),
        "fig-03b-attack-a2.svg": fig_attack_a2(),
        "fig-03c-attack-a3.svg": fig_attack_a3(),
        "fig-03d-attack-a4.svg": fig_attack_a4(),
        "fig-04-crosslayer.svg": fig_crosslayer(),
    }
    paths = [svg.save(name) for name, svg in figs.items()]
    print("SVG：", ", ".join(p.name for p in paths))
    if "--svg-only" not in sys.argv:
        render_pngs(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
