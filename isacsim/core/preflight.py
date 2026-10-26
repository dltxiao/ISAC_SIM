"""硬件信息与算力预检：确保配置不超出本机能力。"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ROOT, SceneCfg


def _meminfo() -> tuple[int, int]:
    total = avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    return total, avail


def hardware_info() -> dict[str, Any]:
    total_mem, avail_mem = _meminfo()
    usage = shutil.disk_usage(ROOT)
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    info: dict[str, Any] = {
        "cpu_count": os.cpu_count() or 1,
        "loadavg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "mem_total_gib": round(total_mem / 2**30, 2),
        "mem_available_gib": round(avail_mem / 2**30, 2),
        "disk_free_gib": round(usage.free / 2**30, 1),
        "disk_total_gib": round(usage.total / 2**30, 1),
    }
    try:
        from .. import sumo_env

        env = sumo_env.ensure()
        info["sumo_version"] = env.version
        info["sumo_home"] = str(env.home)
    except Exception as exc:  # noqa: BLE001
        info["sumo_version"] = None
        info["sumo_error"] = str(exc)
    bench = ROOT / ".isacsim_bench.json"
    if bench.exists():
        try:
            info["bench"] = json.loads(bench.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return info


@dataclass
class Estimate:
    wall_s: float = 0.0
    records: int = 0
    bytes: int = 0
    mem_mb: float = 0.0
    vehicles: int = 0
    sensors: int = 0
    realtime_factor: float = 0.0
    disk_mb: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "wall_s": round(self.wall_s, 2),
            "records": self.records,
            "bytes": self.bytes,
            "mem_mb": round(self.mem_mb, 1),
            "vehicles": self.vehicles,
            "sensors": self.sensors,
            "realtime_factor": round(self.realtime_factor, 1),
            "disk_mb": round(self.disk_mb, 1),
        }


def estimate_scene(scene: SceneCfg, *, compressed: bool = True) -> Estimate:
    """按实测标定的保守系数估算单场景耗时/内存/磁盘。

    基准：60 车、4 传感器、20 Hz、含 JSON 编码 ≈ 55× 实时（单核）。
    """
    veh = max(1, int(scene.vehicles.max * 0.75))
    sensors = len(scene.sensors)
    max_rate = max(s.rate_hz for s in scene.sensors)
    rate_factor = max_rate / 10.0
    # 毫秒/仿真秒
    ms_per_sim_s = 5.0 + 0.05 * veh + 0.65 * veh * sensors * rate_factor
    wall_s = ms_per_sim_s * scene.duration_s / 1000.0
    realtime = scene.duration_s / wall_s if wall_s else 0.0

    frames = sum(s.rate_hz for s in scene.sensors) * scene.duration_s
    records = (
        veh * 0.9 * 10 * scene.duration_s            # CAM
        + frames * max(1, veh * 0.5)                 # 回波/点迹（每目标一条）
        + frames                                     # 目标列表（每帧一条）
        + veh * 10 * scene.duration_s                # GNSS
        + 12 * scene.duration_s                      # 链路/图/任务
    )
    nbytes = int(records * 220)
    mem_mb = 150 + 1.6 * veh
    disk_mb = nbytes / 2**20 * (0.18 if compressed else 1.0)
    return Estimate(wall_s=wall_s, records=int(records), bytes=nbytes,
                    mem_mb=mem_mb, vehicles=veh, sensors=sensors,
                    realtime_factor=realtime, disk_mb=disk_mb)


@dataclass
class PreflightResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    estimate: Estimate | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "messages": self.messages,
            "estimate": self.estimate.as_dict() if self.estimate else None,
        }


def check_scene(scene: SceneCfg, *, max_wall_s: float = 900.0,
                mem_ratio: float = 0.6, disk_ratio: float = 0.1) -> PreflightResult:
    hw = hardware_info()
    est = estimate_scene(scene)
    msgs: list[str] = []
    ok = True
    if est.wall_s > max_wall_s:
        ok = False
        msgs.append(f"预计单场景耗时 {est.wall_s:.0f}s 超过上限 {max_wall_s:.0f}s")
    avail_mb = hw["mem_available_gib"] * 1024
    if est.mem_mb > avail_mb * mem_ratio:
        ok = False
        msgs.append(f"预计内存 {est.mem_mb:.0f}MB 超过可用内存的 {mem_ratio:.0%}")
    if est.disk_mb > hw["disk_free_gib"] * 1024 * disk_ratio:
        ok = False
        msgs.append(f"预计磁盘 {est.disk_mb:.0f}MB 超过剩余磁盘的 {disk_ratio:.0%}")
    if scene.vehicles.max > 200:
        ok = False
        msgs.append("vehicles.max 超过本机建议上限 200")
    if not msgs:
        msgs.append("预算内：可以运行")
    return PreflightResult(ok=ok, messages=msgs, estimate=est)


def estimate_batch(n_scenes: int, scene: SceneCfg, workers: int) -> dict[str, Any]:
    est = estimate_scene(scene)
    workers = max(1, min(workers, (os.cpu_count() or 1)))
    wall = est.wall_s * n_scenes / workers
    return {
        "scenes": n_scenes,
        "workers": workers,
        "total_cpu_s": round(est.wall_s * n_scenes, 1),
        "wall_s": round(wall, 1),
        "disk_gib": round(est.disk_mb * n_scenes / 1024, 2),
        "mem_mb": round(est.mem_mb * workers, 1),
    }
