"""Web 会话管理：单机单会话，后台线程跑仿真，前台只读快照。"""
from __future__ import annotations

import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from ..attacks.base import AttackSpec
from ..config import ROOT, default_scene
from ..core.engine import Simulation
from ..core.preflight import hardware_info, estimate_scene
from ..detect.reference import ReferenceDetector


class SessionError(RuntimeError):
    pass


class SessionManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sim: Simulation | None = None
        self.thread: threading.Thread | None = None
        self.params: dict[str, Any] = {}
        self.run_dir: Path | None = None

    # ------------------------------------------------------------------
    def start(self, *, seed: int = 11, speed: float = 1.0,
              duration: float = 60.0, scene: str = "arterial") -> dict[str, Any]:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise SessionError("已有会话在运行，请先停止或复位")
            scn = default_scene(seed=seed)
            scn.name = scene
            scn.duration_s = float(duration)
            scn.streams.jsonl = True
            scn.streams.gzip = False
            run_id = f"web-{scene}-{seed}-{time.strftime('%H%M%S')}"
            out = ROOT / "out" / "web" / run_id
            detector = ReferenceDetector(scn)
            sim = Simulation(scn, run_id=run_id, out_dir=out, detector=detector,
                             realtime=True, speed=speed, trace=True)
            self.sim = sim
            self.run_dir = out
            self.params = {"seed": seed, "speed": speed, "duration": duration,
                           "scene": scene}
            self.thread = threading.Thread(target=sim.run, name="isacsim-sim", daemon=True)
            self.thread.start()
            return self.snapshot()

    def pause(self) -> dict[str, Any]:
        if self.sim is None:
            raise SessionError("没有会话")
        self.sim.pause()
        return self.snapshot()

    def resume(self) -> dict[str, Any]:
        if self.sim is None:
            raise SessionError("没有会话")
        self.sim.resume()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.sim is None:
                raise SessionError("没有会话")
            self.sim.request_stop()
            if self.thread is not None:
                self.thread.join(timeout=8)
            return self.snapshot()

    def reset(self) -> dict[str, Any]:
        params = dict(self.params)
        with self.lock:
            if self.sim is not None:
                self.sim.request_stop()
                if self.thread is not None:
                    self.thread.join(timeout=8)
        return self.start(**params) if params else self.start()

    def set_speed(self, speed: float) -> dict[str, Any]:
        if self.sim is None:
            raise SessionError("没有会话")
        self.sim.speed = max(0.25, min(8.0, float(speed)))
        self.sim.stats.speed = self.sim.speed
        self.params["speed"] = self.sim.speed
        return self.snapshot()

    # ------------------------------------------------------------------
    def submit_attack(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sim is None or self.sim.state not in ("running", "paused"):
            raise SessionError("仿真未运行")
        spec = AttackSpec.from_dict(payload)
        ids = self.sim.submit_attack(spec)
        return {"ids": ids}

    def cancel_attack(self, attack_id: str) -> bool:
        if self.sim is None:
            return False
        return self.sim.cancel_attack(attack_id)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        if self.sim is None:
            return {"state": "idle", "vehicles": [], "records_total": 0,
                    "bytes_total": 0, "streams": {}, "attacks": {}, "attack_instances": [],
                    "alerts": {}, "alert_total": 0, "latency_ms": {}, "truth_events": 0,
                    "sim_time": 0.0, "duration_s": 0.0, "run_id": None}
        snap = self.sim.snapshot()
        snap["run_id"] = self.sim.run_id
        snap["speed"] = self.sim.speed
        if self.thread is not None and self.thread.is_alive() and snap["state"] == "idle":
            snap["state"] = "starting"
        return snap

    def trace_state(self) -> dict[str, Any]:
        if self.sim is None or self.sim.trace is None:
            return {"schema": "isacsim.trace/1", "enabled": False,
                    "targets": [], "frames": {}, "events": [], "steps": [],
                    "last_step": None, "cursor": 0, "state": "idle",
                    "t": 0.0, "tick": 0}
        return self.sim.trace.snapshot()

    def trace_targets(self) -> list[dict[str, Any]]:
        if self.sim is None or self.sim.trace is None:
            return []
        return self.sim.trace.target_list()

    def trace_events(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        if self.sim is None or self.sim.trace is None:
            return []
        return self.sim.trace.events_after(after, limit)

    def trace_step(self, mode: str = "checkpoint", count: int = 1) -> dict[str, Any]:
        if self.sim is None or self.sim.trace is None:
            raise SessionError("追踪未启用")
        if self.sim.state not in ("running", "paused"):
            raise SessionError("仿真未运行或已停止")
        try:
            return self.sim.step(mode=mode, count=count, timeout=5.0)
        except RuntimeError as exc:
            raise SessionError(str(exc)) from exc

    def net(self) -> dict[str, Any] | None:
        return self.sim.net_json() if self.sim is not None else None

    def hardware(self) -> dict[str, Any]:
        hw = hardware_info()
        scn = default_scene()
        hw["estimate"] = estimate_scene(scn).as_dict()
        return hw

    def runs(self) -> list[dict[str, Any]]:
        base = ROOT / "out" / "web"
        if not base.exists():
            return []
        out = []
        for d in sorted(base.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            events = 0
            tp = d / "truth" / "events.jsonl"
            if tp.exists():
                events = sum(1 for line in tp.read_text(encoding="utf-8").splitlines() if line.strip())
            out.append({"run_id": d.name, "events": events,
                        "size_mb": round(sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 2**20, 2)})
        return out[:50]

    def download(self, run_id: str) -> Path:
        base = ROOT / "out" / "web"
        src = base / run_id
        if not src.is_dir() or base not in src.parents:
            raise SessionError("运行目录不存在")
        target = base / f"{run_id}.zip"
        if not target.exists():
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in src.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(src))
        return target


MANAGER = SessionManager()
