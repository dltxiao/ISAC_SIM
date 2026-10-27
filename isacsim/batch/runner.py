"""批量生成数据集：多进程并行运行场景，落盘观测流 + 真值 + 告警。"""
from __future__ import annotations

import json
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import default_scene
from ..core.engine import Simulation


@dataclass
class Job:
    out: str
    seed: int
    kind: str          # attack / normal
    atype: str | None
    duration: float
    amplitude: float
    split: str
    streams: bool
    step_s: float = 0.05


def _run_one(job: Job) -> dict[str, Any]:
    from ..attacks.base import AttackSpec
    from ..detect.reference import ReferenceDetector
    from ..core.stats import RunStats

    scene = default_scene(seed=job.seed)
    scene.duration_s = job.duration
    scene.step_s = job.step_s
    scene.streams.jsonl = job.streams
    scene.streams.gzip = job.streams
    scene.streams.graph = job.streams
    run_id = f"{job.split}-{job.seed:04d}-{job.atype or 'normal'}"
    out = Path(job.out) / run_id
    sim_stats = RunStats()
    detector = ReferenceDetector(scene, stats=sim_stats)
    sim = Simulation(scene, run_id=run_id, out_dir=out, detector=detector, stats=sim_stats)
    if job.atype:
        start = max(1.0, job.duration * 0.15)
        length = max(4.0, job.duration * 0.45)
        sim.scheduler.submit(AttackSpec(type=job.atype, amplitude=job.amplitude,
                                        duration=length, start=start))
    try:
        sim.run()
    finally:
        detector.dump(out / "alerts.jsonl")
        detector.dump_chains(out / "chains.json")
        (out / "meta.json").write_text(json.dumps({
            "kind": job.kind, "type": job.atype, "seed": job.seed,
            "duration": job.duration, "amplitude": job.amplitude,
            "split": job.split, "records": sim.stats.records_total,
            "bytes": sim.stats.bytes_total, "truth_events": len(sim.truth.events),
            "alerts": len(detector.alerts),
            "realtime_factor": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run": run_id, "records": sim.stats.records_total,
            "truth": len(sim.truth.events), "alerts": len(detector.alerts)}


def build_jobs(out: str, *, attacks: int, normal: int, types: list[str],
               duration: float, amplitude: float, split: str,
               streams: bool, seed0: int = 1) -> list[Job]:
    jobs: list[Job] = []
    n_types = max(1, len(types))
    for i in range(attacks):
        jobs.append(Job(out=out, seed=seed0 + i, kind="attack",
                        atype=types[i % n_types], duration=duration,
                        amplitude=amplitude, split=split, streams=streams))
    for i in range(normal):
        jobs.append(Job(out=out, seed=seed0 + 100_000 + i, kind="normal",
                        atype=None, duration=duration, amplitude=amplitude,
                        split=split, streams=streams))
    return jobs


def run_jobs(jobs: list[Job], workers: int = 8, progress=None) -> list[dict[str, Any]]:
    workers = max(1, min(workers, len(jobs) or 1))
    results: list[dict[str, Any]] = []
    if workers == 1:
        for job in jobs:
            results.append(_run_one(job))
            if progress:
                progress(len(results), len(jobs))
        return results
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers) as pool:
        for r in pool.imap_unordered(_run_one, jobs):
            results.append(r)
            if progress:
                progress(len(results), len(jobs))
    return results
