"""参考检测器：分域规则 + 跨层关联。

明确边界：这是用于验证"环境是否可用、指标是否可达"的基线，
不是项目最终交付的检测模型；它只读取观测流，不读取 truth。
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .. import T0_NS
from ..config import SceneCfg


@dataclass
class Alert:
    t: float
    rule: str
    entity: str
    detail: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 3), "rule": self.rule, "entity": self.entity,
                "detail": self.detail, "latency_ms": round(self.latency_ms, 3)}


class ReferenceDetector:
    def __init__(self, scene: SceneCfg, stats=None) -> None:
        self.scene = scene
        self.stats = stats
        self.t = 0.0
        self.alerts: list[Alert] = []
        self._last_objs: dict[str, list[dict]] = {}
        self._last_cam: dict[str, dict] = {}
        self._cam_track: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        self._obj_streak: dict[tuple[str, int], int] = defaultdict(int)
        self._obj_pos: dict[str, deque] = defaultdict(lambda: deque(maxlen=12))
        self._cross_streak: dict[str, int] = defaultdict(int)
        self._cbr_hist: deque = deque(maxlen=30)
        self._nas_win: deque = deque(maxlen=20000)   # 1 s 内注册请求
        self._nas_rej: deque = deque(maxlen=20000)   # 1 s 内注册拒绝
        self._nas_eval_t: float = -1.0
        self._app_drops: deque = deque(maxlen=4000)   # 1 s 内 platform 因无订阅丢弃
        self._cloud_win: deque = deque(maxlen=200)    # 2 s 内的云控提交（频率判定）
        self._cpm_rx: dict[str, deque] = defaultdict(lambda: deque(maxlen=24))
        self._audit_seq = 0
        self._gnss: dict[str, tuple[float, float, float]] = {}
        self._last_alert: dict[str, float] = {}
        self.static_map = {(round(o["x"], 1), round(o["y"], 1)) for o in scene.static_objects}

    # ------------------------------------------------------------------
    def _alert(self, rule: str, entity: str, detail: dict[str, Any],
               wall_ns: int | None = None) -> None:
        now = self.t
        if now - self._last_alert.get(f"{rule}:{entity}", -1e9) < 1.0:
            return
        self._last_alert[f"{rule}:{entity}"] = now
        latency = 0.0
        if wall_ns:
            latency = (time.monotonic_ns() - wall_ns) / 1e6
        self.alerts.append(Alert(t=now, rule=rule, entity=entity,
                                 detail=detail, latency_ms=latency))
        if self.stats is not None:
            self.stats.add_alert(rule, entity, latency)

    # ------------------------------------------------------------------
    def on_record(self, rec) -> None:
        stream = rec.stream
        p = rec.payload
        self.t = max(self.t, (rec.ts_ns - T0_NS) / 1e9)
        if stream == "sensing.objects":
            self._on_objects(rec, p)
        elif stream == "v2x.cam":
            self._on_cam(rec, p)
        elif stream == "v2x.cpm.rx":
            self._on_cpm_rx(rec, p)
        elif stream == "sensing.gnss":
            self._gnss[p.get("node_id", rec.entity)] = (self.t, float(p.get("x", 0.0)),
                                                        float(p.get("y", 0.0)))
        elif stream == "link.metrics":
            self._on_link(rec, p)
        elif stream == "control.command":
            self._on_control(rec, p)
        elif stream == "core.nas":
            self._on_core_nas(rec, p)
        elif stream == "core.upf":
            self._on_core_upf(rec, p)
        elif stream == "core.qos":
            self._on_core_qos(rec, p)
        elif stream == "app.ingest":
            self._on_app_ingest(rec, p)
        elif stream == "app.control":
            self._on_app_control(rec, p)
        elif stream == "app.audit":
            self._on_app_audit(rec, p)
        elif stream == "app.subscription":
            self._on_app_subscription(rec, p)
        elif stream == "core.nef":
            self._on_core_nef(rec, p)

    # ---- 规则 -----------------------------------------------------------
    def _on_objects(self, rec, p) -> None:
        sid = p.get("sensor_id", rec.source)
        objs = p.get("objects", [])
        self._last_objs[sid] = objs
        for o in objs:
            cls = o.get("cls")
            if cls not in ("stop_sign", "obstacle"):
                continue
            key = (sid, int(o.get("object_id", 0)))
            near_map = any(math.hypot(o["x"] - mx, o["y"] - my) < 3.0
                           for mx, my in self.static_map)
            if near_map:
                continue
            confirmed = False
            for other, oobjs in self._last_objs.items():
                if other == sid:
                    continue
                for oo in oobjs:
                    if math.hypot(oo["x"] - o["x"], oo["y"] - o["y"]) < 2.5:
                        confirmed = True
                        break
                if confirmed:
                    break
            self._obj_streak[key] = self._obj_streak.get(key, 0) + 1 if not confirmed else 0
            if not confirmed and self._obj_streak[key] >= 2:
                kind = next((s.kind for s in self.scene.sensors if s.id == sid), "rsu")
                self._alert("ghost_object", f"{kind}:{sid}",
                            {"x": o["x"], "y": o["y"], "cls": cls}, rec.t_wall_ns)

        # 多传感器互证：同一 entity 在不同站点上的位置应在噪声内一致。
        # 单站回波被篡改（A3/A10/range_bias）时，站间位置会出现米级以上的分裂；
        # 这是跨源证据，不依赖生成器回填的真值坐标。
        for o in objs:
            ent = str(o.get("entity", ""))
            if not ent.startswith("veh:"):
                continue
            hist = self._obj_pos[ent]
            hist.append((self.t, sid, float(o.get("x", 0.0)), float(o.get("y", 0.0))))
            spread, peer = 0.0, None
            for t2, sid2, x2, y2 in hist:
                if sid2 == sid or abs(self.t - t2) > 0.1:
                    continue
                d = math.hypot(o["x"] - x2, o["y"] - y2)
                if d > spread:
                    spread, peer = d, sid2
            if spread > 6.0:
                self._cross_streak[ent] += 1
                if self._cross_streak[ent] >= 3:
                    self._alert("radar_range_bias", ent,
                                {"cross_sensor_m": round(spread, 2),
                                 "sensor": sid, "peer": peer},
                                rec.t_wall_ns)
            else:
                self._cross_streak[ent] = 0

    def _on_cam(self, rec, p) -> None:
        pid = p.get("sender_pseudo", rec.entity)
        pos = (p.get("posx", 0.0), p.get("posy", 0.0))
        self._last_cam[pid] = {"pos": pos, "t": self.t}
        hist = self._cam_track[pid]
        if hist and self.t - hist[-1][0] < 1.0:
            d = math.hypot(pos[0] - hist[-1][1], pos[1] - hist[-1][2])
            if d > 25.0:
                self._alert("cam_position_jump", f"veh:{pid}",
                            {"jump_m": round(d, 1)}, rec.t_wall_ns)
        hist.append((self.t, pos[0], pos[1]))
        g = self._gnss.get(pid)
        if g and self.t - g[0] < 0.5:
            dg = math.hypot(pos[0] - g[1], pos[1] - g[2])
            if dg > 12.0:
                self._alert("cam_gnss_mismatch", f"veh:{pid}",
                            {"cam_gnss_m": round(dg, 1)}, rec.t_wall_ns)

    def _on_link(self, rec, p) -> None:
        cbr = float(p.get("cbr", 0.0))
        self._cbr_hist.append(cbr)
        if len(self._cbr_hist) >= 10:
            rise = cbr - self._cbr_hist[0]
            if cbr > 0.7 and rise > 0.2:
                self._alert("cbr_spike", "net:pc5",
                            {"cbr": round(cbr, 3), "rise": round(rise, 3)}, rec.t_wall_ns)
        if float(p.get("per", 0.0)) > 0.5:
            self._alert("link_degraded", "net:pc5",
                        {"per": p.get("per"), "jam": p.get("jam")}, rec.t_wall_ns)

    def _on_control(self, rec, p) -> None:
        ttc = float(p.get("params", {}).get("ttc_s", 0.0))
        if ttc >= 10.0:
            self._alert("brake_without_hazard", f"veh:{p.get('dst', '')}",
                        {"ttc": ttc, "src": p.get("src")}, rec.t_wall_ns)

    def _on_cpm_rx(self, rec, p) -> None:
        """按 UE 的 CPM 接收质量：5QI 降级 → 丢包/时延上升。"""
        vid = p.get("receiver") or rec.entity.split(":")[-1]
        hist = self._cpm_rx[vid]
        hist.append((self.t, bool(p.get("lost")), float(p.get("delay_ms", 0.0)),
                     int(p.get("5qi", 79))))
        # 窗口 24 帧、至少 16 帧、丢包 ≥14（≈58%）：合法切片调整（p≈0.15）
        # 凑到 14/24 接近 7σ；A6 的 p≈0.62 仍能在 1 s 左右稳定命中。
        if len(hist) < 16:
            return
        lost = sum(1 for _, l, _, _ in hist if l)
        ratio = lost / len(hist)
        five_qi = hist[-1][3]
        if five_qi <= 9 and lost >= 14:
            self._alert("cpm_degraded", f"veh:{vid}",
                        {"5qi": five_qi, "lost_ratio": round(ratio, 2),
                         "delay_ms": round(hist[-1][2], 1)}, rec.t_wall_ns)

    def _on_core_nas(self, rec, p) -> None:
        """注册洪泛：按到达速率 + 拒绝占比判定，不依赖生成器自报的 bot 标记。"""
        mt = p.get("msg_type")
        t = self.t
        cutoff = t - 1.0
        if mt == "registration_request":
            self._nas_win.append((t, str(p.get("ue_id") or "")))
            while self._nas_win and self._nas_win[0][0] < cutoff:
                self._nas_win.popleft()
        elif mt == "registration_reject":
            self._nas_rej.append((t, str(p.get("cause") or "")))
            while self._nas_rej and self._nas_rej[0][0] < cutoff:
                self._nas_rej.popleft()
        else:
            return
        if t - self._nas_eval_t < 0.5:
            return
        self._nas_eval_t = t
        n = len(self._nas_win)
        rej = len(self._nas_rej)
        if n < 300 or rej < 0.1 * n:
            return
        ids = {ue for _, ue in self._nas_win if ue}
        self._alert("nas_registration_flood", "amf:AMF-01", {
            "reg_per_s": n,
            "rejects_per_s": rej,
            "reject_ratio": round(rej / max(1, n), 3),
            "distinct_ue": len(ids),
            "id_diversity": round(len(ids) / max(1, n), 3),
        }, rec.t_wall_ns)

    def _on_core_upf(self, rec, p) -> None:
        allowed = set(getattr(self.scene.core, "allowed_dns", ["dn:DN-01"]))
        if p.get("msg_type") == "route_change" and p.get("route") not in allowed:
            self._alert("upf_route_drift", p.get("upf_id", rec.entity),
                        {"route": p.get("route")}, rec.t_wall_ns)

    def _on_core_qos(self, rec, p) -> None:
        # 合法割接（5QI 79→8、GFBR 1000→800）不应报警：要求同时砍到 500 kbps 以下。
        if (p.get("msg_type") == "qos_flow_modify"
                and int(p.get("5qi", 79)) <= 9
                and int(p.get("gfbr_kbps") or 0) < 500):
            self._alert("qos_downgrade", "smf:SMF-01",
                        {"5qi": p.get("5qi"), "gfbr_kbps": p.get("gfbr_kbps")},
                        rec.t_wall_ns)

    def _on_app_ingest(self, rec, p) -> None:
        allowed = set(getattr(self.scene.core, "allowed_dns", ["dn:DN-01"]))
        prov = p.get("provenance")
        if prov not in (None, *allowed):
            self._alert("ingest_provenance_mismatch", "platform:V2X-01",
                        {"provenance": p.get("provenance"),
                         "source": p.get("source"),
                         "msg_type": p.get("msg_type")}, rec.t_wall_ns)
        if float(p.get("latency_ms", 0.0)) >= 50.0:
            self._alert("ingest_latency_spike", "platform:V2X-01",
                        {"latency_ms": p.get("latency_ms")}, rec.t_wall_ns)
        # 平台因“没有订阅”而持续丢事件：A8 的后果本身，不再依赖退订事件
        if p.get("cause") == "no_subscription":
            self._app_drops.append(self.t)
            cutoff = self.t - 1.0
            while self._app_drops and self._app_drops[0] < cutoff:
                self._app_drops.popleft()
            if len(self._app_drops) >= 10:
                self._alert("ingest_drop_spike", "platform:V2X-01", {
                    "drops_per_s": len(self._app_drops),
                    "msg_type": p.get("msg_type"),
                }, rec.t_wall_ns)

    def _on_app_audit(self, rec, p) -> None:
        seq = int(p.get("audit_seq", 0))
        if self._audit_seq and seq > self._audit_seq + 1:
            self._alert("audit_gap", "platform:V2X-01",
                        {"from": self._audit_seq, "to": seq}, rec.t_wall_ns)
        self._audit_seq = max(self._audit_seq, seq)

    def _on_app_subscription(self, rec, p) -> None:
        removed = p.get("event_types") or []
        if (p.get("action") == "revoke" and p.get("result") == "accept"
                and len(removed) >= 2):
            self._alert("subscription_tamper", "platform:V2X-01",
                        {"subscription_id": p.get("subscription_id"),
                         "removed": list(removed)}, rec.t_wall_ns)

    def _on_core_nef(self, rec, p) -> None:
        if p.get("msg_type") == "api_unsubscribe" and p.get("result") == "accept":
            self._alert("nef_subscription_tamper", "nef:NEF-01",
                        {"subscription_id": p.get("subscription_id")}, rec.t_wall_ns)

    def _on_app_control(self, rec, p) -> None:
        """多车云控：按“批量 + 频率”判定，不依赖 replayed 等自报标记。

        单次多车下发不足以说明问题（车队控制、绿波调度都合法）；这里要求
        2 s 内同一平台连续 ≥2 批、每批 ≥3 车。合法批量控制的误报仍需靠
        正常集里的难负样本评估（审核 R5）。
        """
        if p.get("result") != "accept":
            return
        n = len(p.get("vehicle_ids") or [])
        if n < 3:
            return
        self._cloud_win.append((self.t, n, p.get("token_id")))
        cutoff = self.t - 2.0
        while self._cloud_win and self._cloud_win[0][0] < cutoff:
            self._cloud_win.popleft()
        if len(self._cloud_win) >= 3:
            self._alert("cloud_control_burst", "platform:V2X-01", {
                "batches_2s": len(self._cloud_win),
                "vehicles": n,
                "token": p.get("token_id"),
                "command": p.get("command"),
            }, rec.t_wall_ns)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        by_rule: dict[str, int] = defaultdict(int)
        for a in self.alerts:
            by_rule[a.rule] += 1
        lat = sorted(a.latency_ms for a in self.alerts if a.latency_ms > 0)
        def q(p: float) -> float:
            if not lat:
                return 0.0
            return round(lat[min(len(lat) - 1, int(p * (len(lat) - 1)))], 3)
        chains = self.chains()
        return {
            "total": len(self.alerts),
            "by_rule": dict(by_rule),
            "latency_ms": {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "n": len(lat)},
            "recent": [a.to_dict() for a in self.alerts[-30:]],
            "chain_count": len(chains),
            "chains": chains[-10:],
        }

    def chains(self) -> list[dict[str, Any]]:
        from .chain import build_chains
        return [c.to_dict() for c in build_chains(self.alerts)]

    def dump_chains(self, path) -> None:
        import json
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.chains(), ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def dump(self, path) -> None:
        import json
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for a in self.alerts:
                fh.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
