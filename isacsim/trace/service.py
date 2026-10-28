"""运行追踪服务：把仿真状态投影成 WatchFrame / TraceEvent / StepFrame。

设计文档见 docs/trace/。本模块只观察，不修改 payload、不改变 RNG、不读取检测器
之外的真值（当前版本不暴露真值）。
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any


SENSOR_FIELDS = [
    {"name": "object_id", "type": "int", "width": 8},
    {"name": "cls", "type": "enum", "width": 11},
    {"name": "x", "type": "float", "unit": "m", "precision": 1},
    {"name": "y", "type": "float", "unit": "m", "precision": 1},
    {"name": "vx", "type": "float", "unit": "m/s", "precision": 1},
    {"name": "entity", "type": "string", "role": "origin"},
    {"name": "ghost", "type": "bool", "role": "flag"},
]

FLEET_FIELDS = [
    {"name": "id", "type": "string", "role": "identity"},
    {"name": "cls", "type": "enum", "width": 9},
    {"name": "speed", "type": "float", "unit": "m/s", "precision": 2},
    {"name": "x", "type": "float", "unit": "m", "precision": 1},
    {"name": "y", "type": "float", "unit": "m", "precision": 1},
    {"name": "flags", "type": "string"},
    {"name": "affected", "type": "bool", "role": "flag"},
]


def _now() -> float:
    return time.monotonic()


def _safe(value: Any) -> Any:
    """把常见 numpy 标量/嵌套结构转成 JSON 友好值。"""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return str(value)


def _diff_rows(prev: list[dict], rows: list[dict], key: str) -> dict[str, Any]:
    before = {str(r.get(key)): r for r in prev}
    after = {str(r.get(key)): r for r in rows}
    added = [r[key] for k, r in after.items() if k not in before]
    removed = [r[key] for k, r in before.items() if k not in after]
    changed = []
    for k, r in after.items():
        if k not in before:
            continue
        old = before[k]
        fields = {}
        for f, v in r.items():
            if f in ("x", "y", "vx", "vy", "speed"):
                if abs(float(v) - float(old.get(f, v))) > 0.2:
                    fields[f] = {"before": old.get(f), "after": v}
            elif old.get(f) != v:
                fields[f] = {"before": old.get(f), "after": v}
        if fields:
            changed.append({"key": r.get(key), "fields": fields})
    return {"added": added, "removed": removed, "changed": changed}


def _diff_struct(prev: dict | None, cur: dict) -> dict[str, Any]:
    if not prev:
        return {"added": [], "removed": [], "changed": []}
    added = [k for k in cur if k not in prev]
    removed = [k for k in prev if k not in cur]
    changed = []
    for k, v in cur.items():
        if k in prev and prev[k] != v:
            changed.append({"key": k, "before": prev[k], "after": v})
    return {"added": added, "removed": removed, "changed": changed}


class TraceService:
    """单会话、单写者（仿真线程）、多读者（Web）。"""

    def __init__(self, sim) -> None:
        self.sim = sim
        self.lock = threading.RLock()
        self.enabled = True
        self.seq = 0
        self._t = 0.0
        self._tick = 0
        self.targets: dict[str, dict[str, Any]] = {}
        self.frames: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=3000)
        self.steps: deque[dict[str, Any]] = deque(maxlen=50)

        self._prev_rows: dict[str, list[dict]] = {}
        self._prev_struct: dict[str, dict] = {}
        self._last_sensor_t: dict[str, float] = {}
        self._last_fleet_t = -1.0
        self._last_pc5_t = -1.0
        self._last_scene_t = -1.0
        self._last_victim_t = -1.0

        self._attacks: dict[str, dict[str, Any]] = {}
        self._victim_id: str | None = None
        self._affected: set[str] = set()
        self._commanded: set[str] = set()
        self._last_cmd: dict[str, dict[str, Any]] = {}
        self._speed_hist: dict[str, deque] = {}
        self._speed_drop: dict[str, float] = {}
        self._drop_reported: set[str] = set()
        self._alerts_seen = 0
        self._ghost_seen: set[str] = set()
        self._stage_done: set[str] = set()
        self._evidence: dict[str, dict] = {}
        self._evidence_floor = 0
        self._cbr_base: float | None = None
        self._offered_base: float | None = None
        self._cbr_rise_reported = False

        self._step_mode: str | None = None
        self._step_from_seq = 0
        self._step_from_t = 0.0
        self._step_from_tick = 0
        self._step_before: dict[str, dict] = {}
        self._step_started = 0.0
        self._step_index = 0
        self.last_step: dict[str, Any] | None = None
        self._pause_reason: str | None = None

        self._build_global_targets()

    # ------------------------------------------------------------------
    # targets
    def _target(self, tid: str, kind: str, group: str, label: str,
                *, description: str = "", fields=None, key_field=None,
                order: int = 100) -> None:
        if tid in self.targets:
            return
        self.targets[tid] = {
            "id": tid, "kind": kind, "group": group, "label": label,
            "description": description, "fields": fields or [],
            "key_field": key_field, "order": order,
        }

    def _build_global_targets(self) -> None:
        self._target("scene:summary", "struct", "Scene", "Scene · summary",
                     description="仿真时间、车辆、攻击、告警总览", order=0)
        for i, sensor in enumerate(self.sim.scene.sensors):
            self._target(f"sensor:{sensor.id}.objects", "table", "Sensors",
                         f"{sensor.id} · sensing.objects",
                         description=f"{sensor.id} 输出的目标列表（post-hook）",
                         fields=SENSOR_FIELDS, key_field="object_id",
                         order=10 + i)
        self._target("veh:fleet", "table", "Vehicles", "Fleet · vehicles",
                     description="在线车辆；默认按受影响/ID 排序",
                     fields=FLEET_FIELDS, key_field="id", order=30)
        self._target("net:pc5", "struct", "Network", "PC5 · link metrics",
                     description="信道负载与链路质量", order=40)
        self._target("det:alerts", "event", "Detection", "Detector · alerts",
                     description="参考检测器的最近告警", order=50)

    def _add_attack_targets(self, inst) -> None:
        aid = inst.id
        self._target(f"atk:{aid}", "struct", "Attacks",
                     f"{inst.atype} · {aid}",
                     description=getattr(inst, "label", ""), order=60)
        victim = getattr(inst, "veh_id", None)
        if victim and self._victim_id is None:
            self._victim_id = victim
        if victim:
            self._target(f"veh:{victim}.state", "struct", "Vehicles",
                         f"{victim} · state",
                         description="被攻击/受影响车辆的状态", order=70)

    # ------------------------------------------------------------------
    # primitives
    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def _event(self, kind: str, summary: str, *, data: dict | None = None,
               target_id: str | None = None, attack_id: str | None = None,
               severity: str = "info", t: float | None = None) -> dict:
        ev = {
            "seq": self._next(),
            "t": round(self._t if t is None else t, 3),
            "tick": self._tick,
            "kind": kind,
            "summary": summary,
            "severity": severity,
            "target_id": target_id,
            "attack_id": attack_id,
            "data": _safe(data or {}),
        }
        self.events.append(ev)
        return ev

    def _frame(self, tid: str, values: dict, *, summary: str = "",
               delta: dict | None = None, status: str = "changed",
               t: float | None = None) -> dict | None:
        if tid not in self.targets:
            return None
        prev = self.frames.get(tid)
        prev_values = prev.get("values") if prev else None
        if prev_values == values:
            return prev
        f = {
            "seq": self._next(),
            "t": round(self._t if t is None else t, 3),
            "tick": self._tick,
            "target_id": tid,
            "status": status if prev else "new",
            "summary": summary,
            "values": _safe(values),
            "delta": delta or {"added": [], "removed": [], "changed": []},
        }
        self.frames[tid] = f
        return f

    # ------------------------------------------------------------------
    # record observer
    def on_record(self, rec) -> None:
        if not self.enabled:
            return
        p = rec.payload
        t = self.sim.t
        with self.lock:
            if rec.stream == "control.command":
                vid = p.get("dst", rec.entity.split(":", 1)[-1])
                if not vid:
                    return
                self._affected.add(vid)
                self._commanded.add(vid)
                self._last_cmd[vid] = {
                    "seq": p.get("seq"), "reason": p.get("reason"),
                    "decel": (p.get("params") or {}).get("decel_mps2"),
                    "ttc": (p.get("params") or {}).get("ttc_s"),
                    "src": p.get("src"), "t": round(t, 3),
                }
                self._event(
                    "control.command",
                    f"{vid} brake · ttc={self._last_cmd[vid]['ttc']}",
                    data=self._last_cmd[vid], target_id=f"veh:{vid}.state",
                    attack_id=self._attack_for_vehicle(vid), t=t,
                )
            elif rec.stream == "v2x.denm" and p.get("forged"):
                self._event(
                    "record.forged_denm",
                    f"forged DENM @ {p.get('posx')}, {p.get('posy')}",
                    data={"sender": p.get("sender_pseudo"), "cause": p.get("cause")},
                    t=t,
                )
            elif rec.stream in ("auth.cert", "auth.fingerprint") and (p.get("cloned") or p.get("event") == "clone"):
                self._event(
                    "record.clone", f"{rec.stream} cloned @ {rec.entity}",
                    data={"stream": rec.stream, "entity": rec.entity,
                          "cloned": True},
                    attack_id=self._attack_for_vehicle(rec.entity.split(":", 1)[-1]),
                    t=t,
                )

    def _attack_for_vehicle(self, vid: str) -> str | None:
        for inst in self.sim.scheduler.instances:
            if getattr(inst, "veh_id", None) == vid and not getattr(inst, "finished", False):
                return inst.id
        return None

    def _attack_for_entity(self, entity: str) -> str | None:
        for inst in self.sim.scheduler.instances:
            if getattr(inst, "finished", False):
                continue
            if entity.startswith("rsu:") and getattr(inst, "rsu_id", None) == entity[4:]:
                return inst.id
            if entity.startswith("veh:") and getattr(inst, "veh_id", None) == entity[4:]:
                return inst.id
        return None

    # ------------------------------------------------------------------
    # tick observer
    def begin_step(self, mode: str) -> None:
        with self.lock:
            self._step_mode = mode
            self._step_from_seq = self.seq
            self._step_from_t = self.sim.t
            self._step_from_tick = self.sim.tick
            self._step_before = copy.deepcopy(self.frames)
            self._step_started = _now()
            self._pause_reason = None

    def after_scheduler(self, ctx) -> None:
        """攻击启动之后、各层之前：先登记攻击/受害车，保证事件顺序。"""
        if not self.enabled:
            return
        with self.lock:
            self._t = ctx.t
            self._tick = ctx.tick
            self._update_attacks()

    def after_tick(self, ctx) -> None:
        if not self.enabled:
            return
        with self.lock:
            self._t = self.sim.t
            self._tick = ctx.tick
            self._update_attacks()
            self._update_sensors()
            self._update_vehicles(ctx)
            self._update_pc5(ctx)
            self._update_detector()
            self._update_scene()
            if self._step_mode == "checkpoint":
                self._collect_evidence()
                self._maybe_finish_step(ctx)

    # ------------------------------------------------------------------
    def _reset_attack_state(self) -> None:
        self._stage_done.clear()
        self._evidence.clear()
        self._evidence_floor = self.seq
        self._ghost_seen.clear()
        self._cbr_rise_reported = False
        self._commanded.clear()
        self._affected.clear()
        self._speed_drop.clear()
        self._drop_reported.clear()
        self._last_cmd.clear()
        pc5 = self.frames.get("net:pc5")
        if pc5:
            vals = pc5.get("values") or {}
            self._cbr_base = vals.get("cbr")
            self._offered_base = vals.get("offered")

    def _update_attacks(self) -> None:
        for inst in list(self.sim.scheduler.instances):
            aid = inst.id
            snap = inst.snapshot()
            new = aid not in self._attacks
            ended = bool(snap.get("finished"))
            info = {
                "id": aid, "type": snap.get("type"), "label": snap.get("label"),
                "kind": snap.get("kind"), "t0": snap.get("t0"),
                "t1": snap.get("t1"), "started": snap.get("started"),
                "finished": ended, "amplitude": snap.get("amplitude"),
                "target": snap.get("target"),
                "params": snap.get("params") or {},
                "rsu": getattr(inst, "rsu_id", None),
                "victim": getattr(inst, "veh_id", None),
                "ghost_count": len(getattr(inst, "ghosts", []) or []),
                "extra_load": getattr(inst, "extra_load", None),
            }
            old = self._attacks.get(aid)
            if new:
                self._attacks[aid] = {"started": bool(snap.get("started")),
                                      "finished": ended}
                self._add_attack_targets(inst)
                if snap.get("started"):
                    self._reset_attack_state()
                if snap.get("started"):
                    self._event(
                        "attack.start",
                        f"{info['type']} {aid} · rsu={info['rsu'] or '-'} victim={info['victim'] or '-'}",
                        data=info, attack_id=aid, target_id=f"atk:{aid}",
                        severity="attack",
                    )
            elif old and old["finished"] != ended:
                self._attacks[aid]["finished"] = ended
                if ended:
                    self._event("attack.end", f"{info['type']} {aid} 结束",
                                data=info, attack_id=aid, target_id=f"atk:{aid}",
                                severity="attack")
            if old and not old.get("started") and snap.get("started"):
                self._reset_attack_state()
                self._attacks[aid]["started"] = True
                self._add_attack_targets(inst)
                self._event(
                    "attack.start",
                    f"{info['type']} {aid} · rsu={info['rsu'] or '-'} victim={info['victim'] or '-'}",
                    data=info, attack_id=aid, target_id=f"atk:{aid}",
                    severity="attack",
                )
            self._frame(f"atk:{aid}", info,
                        summary=f"{info['type']} · {'结束' if ended else ('进行' if info['started'] else '等待')}")
            if info["victim"] and self._victim_id is None:
                self._victim_id = info["victim"]
                self._target(f"veh:{info['victim']}.state", "struct", "Vehicles",
                             f"{info['victim']} · state", order=70)

    def _update_sensors(self) -> None:
        for sensor in self.sim.scene.sensors:
            tid = f"sensor:{sensor.id}.objects"
            ft = self.sim.sensing.last_frame_t.get(sensor.id)
            if ft is None:
                continue
            if abs(float(ft) - self._last_sensor_t.get(sensor.id, -1.0)) < 1e-9:
                continue
            self._last_sensor_t[sensor.id] = float(ft)
            objs = self.sim.sensing.last_objects.get(sensor.id, []) or []
            rows = [{
                "object_id": o.get("object_id"),
                "cls": o.get("cls"),
                "x": o.get("x"),
                "y": o.get("y"),
                "vx": o.get("vx"),
                "vy": o.get("vy"),
                "entity": o.get("entity"),
                "ghost": str(o.get("entity", "")).startswith("ghost:"),
            } for o in objs]
            rows.sort(key=lambda r: (str(r.get("entity", "")), r.get("object_id") or 0))
            prev = self._prev_rows.get(tid, [])
            delta = _diff_rows(prev, rows, "object_id")
            ghost_count = sum(1 for r in rows if r["ghost"])
            self._prev_rows[tid] = rows
            self._frame(tid, {"rows": rows[:80], "object_count": len(rows),
                              "ghost_count": ghost_count, "frame_t": round(float(ft), 3)},
                        summary=f"{len(rows)} objects / {ghost_count} ghosts",
                        delta=delta,
                        t=float(ft))
            added_ghosts = [r for r in rows if r["ghost"] and
                            str(r["object_id"]) in {str(x) for x in delta["added"]}]
            if added_ghosts and tid not in self._ghost_seen:
                self._ghost_seen.add(tid)
                self._event(
                    "ghost.added",
                    f"{sensor.id} +{len(added_ghosts)} ghost target(s)",
                    data={"sensor": sensor.id, "ghosts": [
                        {"object_id": r["object_id"], "cls": r["cls"],
                         "x": r["x"], "y": r["y"]} for r in added_ghosts]},
                    target_id=tid, attack_id=self._attack_for_entity(f"rsu:{sensor.id}"),
                    severity="attack",
                )

    def _update_vehicles(self, ctx) -> None:
        vehicles = ctx.vehicles or {}
        rows = []
        for v in vehicles.values():
            hist = self._speed_hist.setdefault(v.vid, deque(maxlen=40))
            hist.append((self._t, float(v.speed)))
            while hist and self._t - hist[0][0] > 0.5:
                hist.popleft()
            drop = float(hist[0][1]) - float(v.speed) if hist else 0.0
            if drop > self._speed_drop.get(v.vid, 0.0):
                self._speed_drop[v.vid] = drop
            flags = list(self.sim.flags.get(v.vid, []))
            affected = v.vid in self._commanded or bool(flags)
            if affected:
                self._affected.add(v.vid)
            rows.append({
                "id": v.vid, "cls": v.cls, "speed": round(float(v.speed), 2),
                "x": round(float(v.x), 1), "y": round(float(v.y), 1),
                "flags": ",".join(flags), "affected": affected,
            })
            if drop >= 0.5 and v.vid in self._commanded and v.vid not in self._drop_reported:
                self._drop_reported.add(v.vid)
                self._event(
                    "vehicle.speed_drop",
                    f"{v.vid} speed -{drop:.2f} m/s",
                    data={"id": v.vid, "drop": round(drop, 3),
                          "speed": round(float(v.speed), 3)},
                    target_id=f"veh:{v.vid}.state",
                    attack_id=self._attack_for_vehicle(v.vid),
                    severity="effect",
                )
        rows.sort(key=lambda r: (not r["affected"], r["id"]))
        changed = set(r["id"] for r in rows if r["affected"]) != set(
            r["id"] for r in self._prev_rows.get("veh:fleet", []) if r.get("affected"))
        if self._t - self._last_fleet_t >= 0.1 or changed:
            self._last_fleet_t = self._t
            prev = self._prev_rows.get("veh:fleet", [])
            delta = _diff_rows(prev, rows, "id")
            self._prev_rows["veh:fleet"] = rows
            self._frame("veh:fleet", {"rows": rows[:40], "total": len(rows),
                                      "affected": sum(1 for r in rows if r["affected"]),
                                      "truncated": len(rows) > 40},
                        summary=f"{len(rows)} vehicles · {sum(1 for r in rows if r['affected'])} affected",
                        delta=delta)

        victim = self._victim_id
        if victim and victim in vehicles and self._t - self._last_victim_t >= 0.1:
            self._last_victim_t = self._t
            v = vehicles[victim]
            cmd = self._last_cmd.get(victim)
            values = {
                "id": victim, "speed": round(float(v.speed), 3),
                "x": round(float(v.x), 2), "y": round(float(v.y), 2),
                "heading": round(float(v.heading_deg), 1),
                "flags": self.sim.flags.get(victim, []),
                "affected": victim in self._affected,
                "ttc": (cmd or {}).get("ttc"),
                "last_command": cmd,
                "speed_drop": round(self._speed_drop.get(victim, 0.0), 3),
            }
            prev = self._prev_struct.get(f"veh:{victim}.state")
            delta = _diff_struct(prev, values)
            self._prev_struct[f"veh:{victim}.state"] = values
            self._frame(f"veh:{victim}.state", values,
                        summary=f"{values['speed']:.2f} m/s", delta=delta)

    def _update_pc5(self, ctx) -> None:
        v = self.sim.v2x
        extra = float(ctx.extras.get("v2x_extra_load", 0.0) or 0.0)
        values = {
            "cbr": round(float(v.cbr), 3), "offered": round(float(v.offered), 1),
            "per": round(float(v.per), 4), "delay_ms": round(float(v.delay_ms), 2),
            "dcc": round(float(v.dcc_factor), 2),
            "jam": round(float(ctx.extras.get("link_jam", 0.0) or 0.0), 3),
            "extra_load": round(extra, 1),
        }
        if self._cbr_base is None:
            self._cbr_base = values["cbr"]
            self._offered_base = values["offered"]
            values["cbr_base"] = self._cbr_base
            values["offered_base"] = self._offered_base
        else:
            values["cbr_base"] = self._cbr_base
            values["offered_base"] = self._offered_base
        if self._t - self._last_pc5_t >= 0.1:
            self._last_pc5_t = self._t
            prev = self._prev_struct.get("net:pc5")
            delta = _diff_struct(prev, values)
            self._prev_struct["net:pc5"] = values
            self._frame("net:pc5", values,
                        summary=f"CBR {values['cbr']:.3f} · offered {values['offered']:.0f}",
                        delta=delta)
            if not self._cbr_rise_reported and (
                    values["cbr"] - (prev or {}).get("cbr", values["cbr"]) > 0.1 or
                    values["offered"] - (prev or {}).get("offered", values["offered"]) > 100):
                self._cbr_rise_reported = True
                self._event("pc5.rise", "PC5 offered/CBR 上升",
                            data={"cbr": values["cbr"], "offered": values["offered"],
                                  "extra_load": extra},
                            target_id="net:pc5", severity="effect")

    def _update_detector(self) -> None:
        det = self.sim.detector
        if det is None:
            return
        alerts = getattr(det, "alerts", []) or []
        if len(alerts) <= self._alerts_seen:
            return
        new = alerts[self._alerts_seen:]
        self._alerts_seen = len(alerts)
        for a in new:
            d = a.to_dict() if hasattr(a, "to_dict") else dict(a)
            self._event("alert", f"{d.get('rule')} @ {d.get('entity')}",
                        data=d, target_id="det:alerts",
                        attack_id=self._attack_for_entity(str(d.get("entity", ""))),
                        severity="alert", t=float(d.get("t", self._t)))
        vals = [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in alerts[-30:]]
        self._frame("det:alerts", {"alerts": vals, "total": len(alerts)},
                    summary=f"{len(alerts)} alerts")

    def _update_scene(self) -> None:
        if self._t - self._last_scene_t < 0.1:
            return
        self._last_scene_t = self._t
        values = {
            "t": round(self._t, 3), "tick": self._tick,
            "state": self.sim.state,
            "vehicles": len(self.sim.sumo.vehicles()) if self.sim.sumo is not None else 0,
            "attacks": len(self.sim.scheduler.instances),
            "active_attacks": len([i for i in self.sim.scheduler.instances
                                   if i.started and not i.finished]),
            "alerts": (len(self.sim.detector.alerts) if self.sim.detector else 0),
        }
        self._frame("scene:summary", values,
                    summary=f"{values['vehicles']} veh · t={values['t']:.2f}")

    # ------------------------------------------------------------------
    # stepping
    def _collect_evidence(self) -> None:
        """把“已经发生过”的关键事件按阶段归档，供分步讲解复用。"""
        mapping = {
            "ghost.added": "S1",
            "control.command": "S2",
            "pc5.rise": "S3",
            "vehicle.speed_drop": "S4",
            "alert": "S5",
            "attack.end": "END",
        }
        for ev in self.events:
            if ev.get("seq", 0) <= self._evidence_floor:
                continue
            stage = mapping.get(ev.get("kind"))
            if stage and stage not in self._evidence:
                self._evidence[stage] = ev

    def _maybe_finish_step(self, ctx) -> None:
        self._collect_evidence()
        reason = None
        title = None
        narrative = None
        attack_id = None
        evidence = None
        order = ["S1", "S2", "S3", "S4", "S5", "END"]
        for stage in order:
            if stage in self._stage_done:
                continue
            ev = self._evidence.get(stage)
            if ev is None:
                continue
            # A1 的告警常常早于速度下降 ~0.2 s；先让 S4 有机会出现，
            # 0.5 s 后仍没有减速再允许 S5，避免演示顺序变成“先告警后减速”。
            if (stage == "S5" and "S2" in self._evidence
                    and "S4" not in self._stage_done
                    and self._t - float(self._evidence["S2"].get("t", 0.0)) < 0.5):
                continue
            reason = stage
            evidence = ev
            attack_id = ev.get("attack_id")
            narrative = ev.get("summary")
            title = {
                "S1": "目标列表被篡改",
                "S2": "ADAS 误判制动",
                "S3": "通信负载上升",
                "S4": "物理后果：车辆减速",
                "S5": "检测器告警",
                "END": "攻击结束",
            }[stage]
            break
        if reason is None and ctx.t - self._step_from_t >= 2.0:
            reason, title = "MAX", "继续推进"
            narrative = "本段没有命中新的检查点，已推进 2 s"
        if reason is None:
            return
        if reason != "MAX":
            self._stage_done.add(reason)
        events = [e for e in self.events if e["seq"] > self._step_from_seq]
        if evidence and evidence["seq"] <= self._step_from_seq:
            events = [evidence] + events
        # 去重并按 seq 排序
        seen = set()
        uniq = []
        for e in events:
            if e["seq"] in seen:
                continue
            seen.add(e["seq"])
            uniq.append(e)
        uniq.sort(key=lambda e: e["seq"])
        self._step_index += 1
        after = copy.deepcopy(self.frames)
        step = {
            "schema": "isacsim.step/1",
            "step_id": f"{attack_id or 'A1'}:{reason}:{self._step_index}",
            "index": self._step_index,
            "title": title,
            "reason": reason,
            "from": {"seq": self._step_from_seq, "tick": self._step_from_tick,
                     "t": round(self._step_from_t, 3)},
            "to": {"seq": self.seq, "tick": self._tick, "t": round(self._t, 3)},
            "attack_id": attack_id,
            "stage_id": reason,
            "narrative": narrative,
            "before": self._step_before,
            "after": after,
            "events": uniq[-80:],
            "alerts": [e for e in uniq if e["kind"] == "alert"],
        }
        self.steps.append(step)
        self.last_step = step
        self._step_mode = None
        self._pause_reason = f"checkpoint:{reason}"
        self.sim.pause(self._pause_reason)

    # ------------------------------------------------------------------
    # read API
    @staticmethod
    def _step_public(step: dict | None) -> dict | None:
        if not step:
            return None
        out = {k: v for k, v in step.items()
               if k not in ("before", "after", "events", "alerts")}
        out["event_count"] = len(step.get("events", []))
        out["alert_count"] = len(step.get("alerts", []))
        return out

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            targets = sorted(self.targets.values(), key=lambda t: (t["order"], t["id"]))
            return {
                "schema": "isacsim.trace/1",
                "enabled": self.enabled,
                "cursor": self.seq,
                "t": round(self._t, 3),
                "tick": self._tick,
                "duration_s": self.sim.scene.duration_s,
                "state": self.sim.state,
                "pause_reason": self._pause_reason,
                "step_mode": self._step_mode,
                "targets": targets,
                "frames": copy.deepcopy(self.frames),
                "events": [copy.deepcopy(e) for e in list(self.events)[-120:]],
                "steps": [self._step_public(s) for s in list(self.steps)[-10:]],
                "last_step": self._step_public(self.last_step),
                "attacks": copy.deepcopy(self._attacks),
                "affected": sorted(self._affected),
            }

    def events_after(self, after: int = 0, limit: int = 200) -> list[dict]:
        with self.lock:
            evs = [copy.deepcopy(e) for e in self.events if e["seq"] > after]
            return evs[-limit:]

    def target_list(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(sorted(self.targets.values(),
                                        key=lambda t: (t["order"], t["id"])))
