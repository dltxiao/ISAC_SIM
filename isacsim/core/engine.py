"""仿真主循环：SUMO + 各层 + 攻击调度 + 真值 + 统计。"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import ROOT, SceneCfg
from ..truth.model import TruthStore
from .context import HookBus, SimContext
from .preflight import estimate_scene
from .records import NullSink, Record, make_sink
from .stats import RunStats


class Simulation:
    def __init__(self, scene: SceneCfg, *, run_id: str | None = None,
                 out_dir: str | Path | None = None, sink=None,
                 stats: RunStats | None = None, detector=None,
                 realtime: bool = False, speed: float = 1.0) -> None:
        self.scene = scene
        self.run_id = run_id or f"{scene.name}-{scene.seed}-{int(time.time())}"
        self.out_dir = Path(out_dir) if out_dir else None
        self.stats = stats or RunStats()
        if sink is not None:
            self.sink = sink
        elif self.out_dir:
            self.sink = make_sink(self.out_dir, self.run_id, scene.streams, self.stats)
        else:
            from .stats import CountingSink
            self.sink = CountingSink(NullSink(), self.stats)
        self.detector = detector
        if detector is not None and getattr(detector, "stats", None) is None:
            detector.stats = self.stats
        self.realtime = realtime
        self.speed = float(speed)

        self.sumo = None
        self.t = 0.0
        self.tick = 0
        self.truth = TruthStore(self.out_dir / "truth" if self.out_dir else None)
        self.hooks = HookBus()
        self.flags: dict[str, list[str]] = {}

        self._stop = threading.Event()
        self._paused = threading.Event()
        self.state = "idle"
        self.error: str | None = None
        self._started_wall = 0.0
        self._net_json: dict[str, Any] | None = None

        from ..attacks.scheduler import AttackScheduler
        from ..layers.auth import AuthLayer
        from ..layers.control import ControlLayer
        from ..layers.core import CoreLayer
        from ..layers.edge import EdgeLayer
        from ..layers.sensing import SensingLayer
        from ..layers.task import TaskLayer
        from ..layers.uu import UuLayer
        from ..layers.v2x import V2XLayer

        self.sensing = SensingLayer(scene)
        self.v2x = V2XLayer(scene)
        self.uu = UuLayer(scene)
        self.core = CoreLayer(scene) if scene.core.enabled else None
        self.auth = AuthLayer(scene)
        self.control = ControlLayer(scene, self.sensing)
        self.task = TaskLayer(scene)
        self.edge = EdgeLayer(scene)
        self.scheduler = AttackScheduler(scene, self.stats, self.truth, scene.seed, self.run_id)

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.state in ("running", "paused"):
            return
        from ..world.sumo_backend import SumoBackend

        net = self.scene.asset(self.scene.net)
        route = self.scene.asset(self.scene.route)
        if not net.exists():
            raise FileNotFoundError(f"路网不存在：{net}（先运行 scripts/build_net.sh）")
        if not route.exists():
            raise FileNotFoundError(f"路径文件不存在：{route}")
        self.sumo = SumoBackend(net, route, seed=self.scene.seed, step_s=self.scene.step_s,
                               log_dir=(self.out_dir / "log") if self.out_dir else None)
        self.sumo.start()
        self._net_json = self.sumo.net_json()
        tls_ids = [t["id"] for t in self._net_json.get("traffic_lights", [])]
        if tls_ids:
            self.scene.map["signals"] = tls_ids
        else:
            self.scene.map.pop("signals", None)
        self._started_wall = time.monotonic()
        self.state = "running"
        self.stats.state = "running"
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / "scenario.json").write_text(
                json.dumps(_scene_dict(self.scene), ensure_ascii=False, indent=2), encoding="utf-8")
            (self.out_dir / "net.json").write_text(
                json.dumps(self._net_json, ensure_ascii=False), encoding="utf-8")
            (self.out_dir / "map.json").write_text(
                json.dumps(self.scene.map, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    def step_once(self) -> bool:
        if self.sumo is None:
            raise RuntimeError("仿真尚未启动")
        if self.t >= self.scene.duration_s - 1e-9:
            return False
        if not self.sumo.running:
            self.error = self.sumo.tail_log() or "SUMO 已退出"
            return False
        self.sumo.step()
        vehicles = self.sumo.vehicles()
        ctx = SimContext(
            t=self.t, tick=self.tick, step_s=self.scene.step_s,
            run_id=self.run_id, scene=self.scene, sink=self.sink,
            stats=self.stats, hooks=self.hooks, vehicles=vehicles,
            sumo=self.sumo, truth=self.truth, detector=self.detector,
        )
        ctx.extras["layers"] = {"sensing": self.sensing, "control": self.control,
                                "v2x": self.v2x, "uu": self.uu, "core": self.core}
        try:
            self.scheduler.tick(ctx)
            self.sensing.tick(ctx)
            self.v2x.tick(ctx)
            self.uu.tick(ctx)
            if self.core is not None:
                self.core.tick(ctx)
            self.auth.tick(ctx)
            self.control.tick(ctx)
            self.task.tick(ctx)
            if self.scene.streams.graph:
                self.edge.tick(ctx)
        finally:
            self._update_flags()
        self.t += self.scene.step_s
        self.tick += 1
        self.stats.sim_time = self.t
        self.stats.vehicles = len(vehicles)
        return True

    def _update_flags(self) -> None:
        flags: dict[str, list[str]] = {}
        for inst in self.scheduler.instances:
            if not inst.started or inst.finished:
                continue
            victim = inst.spec.params.get("victim") or getattr(inst, "veh_id", "")
            if victim:
                flags.setdefault(victim, []).append(inst.atype)
        self.flags = flags

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.start()
        wall0 = time.monotonic()
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    if self.realtime:
                        wall0 = time.monotonic() - self.t / max(0.05, self.speed)
                    time.sleep(0.02)
                    continue
                if not self.step_once():
                    break
                if self.realtime:
                    target = wall0 + self.t / max(0.05, self.speed)
                    delay = target - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = str(exc)
            self.stats.state = "error"
            self.stats.note("error", str(exc))
        finally:
            self.stop()

    # ------------------------------------------------------------------
    def pause(self) -> None:
        self._paused.set()
        self.state = "paused"
        self.stats.state = "paused"

    def resume(self) -> None:
        self._paused.clear()
        self.state = "running"
        self.stats.state = "running"

    def request_stop(self) -> None:
        self._stop.set()

    def stop(self) -> None:
        if self.state == "stopped":
            return
        self.state = "stopped"
        self.stats.state = "stopped"
        try:
            if self.sumo is not None:
                self.sumo.close()
        finally:
            self.sink.close()
            if self.core is not None:
                self.core.close()
            self.truth.write_manifest({
                "run_id": self.run_id,
                "scene": self.scene.name,
                "seed": self.scene.seed,
                "duration_s": self.scene.duration_s,
                "step_s": self.scene.step_s,
                "records": self.stats.records_total,
                "bytes": self.stats.bytes_total,
                "attacks": self.stats.attacks.snapshot(),
                "estimate": estimate_scene(self.scene).as_dict(),
            })
            self.truth.close()

    # ------------------------------------------------------------------
    def submit_attack(self, spec) -> list[str]:
        return [i.id for i in self.scheduler.submit(spec)]

    def cancel_attack(self, attack_id: str) -> bool:
        return self.scheduler.cancel(attack_id)

    def snapshot(self) -> dict[str, Any]:
        vehicles = []
        if self.sumo is not None:
            for v in self.sumo.vehicles().values():
                vehicles.append({
                    "id": v.vid, "x": round(v.x, 2), "y": round(v.y, 2),
                    "heading": round(v.heading_deg, 1), "speed": round(v.speed, 2),
                    "length": v.length, "width": v.width, "cls": v.cls,
                    "flags": self.flags.get(v.vid, []),
                })
        snap = self.stats.snapshot()
        snap.update({
            "run_id": self.run_id,
            "state": self.state,
            "sim_time": round(self.t, 3),
            "duration_s": self.scene.duration_s,
            "vehicles": vehicles,
            "attack_instances": self.scheduler.snapshot(),
            "truth_events": len(self.truth.events),
            "infrastructure": [
                {"id": s.id, "kind": s.kind, "x": s.x, "y": s.y,
                 "range": s.range_m, "fov": s.fov_deg, "yaw": s.yaw_deg}
                for s in self.scene.sensors
            ],
        })
        if self.core is not None:
            snap["core"] = self.core.snapshot()
        if self.detector is not None:
            snap["detector"] = self.detector.snapshot()
        if self.error:
            snap["error"] = self.error
        return snap

    def net_json(self) -> dict[str, Any] | None:
        return self._net_json

    def wait_done(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.state in ("starting", "running", "paused"):
            if deadline and time.monotonic() > deadline:
                return
            time.sleep(0.05)


def _scene_dict(scene: SceneCfg) -> dict[str, Any]:
    d = asdict(scene)
    d["sensors"] = [asdict(s) for s in scene.sensors]
    return d
