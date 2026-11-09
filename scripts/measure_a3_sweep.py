"""A3 强度—检出率扫描（docs/07 的“仅雷达规则”表）。

用法：
    PYTHONPATH=. .venv/bin/python scripts/measure_a3_sweep.py --seeds 20 --workers 12

输出每个 amplitude 的：
- radar_only：出现 `radar_range_bias` 的比例
- ttc_brake：出现 `reason=ttc` 制动的比例（ADAS 被诱导）
- injected：出现 `reason=injected` 的比例（攻击者补的伪指令）
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isacsim import T0_NS
from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector

AMPS = [0.20, 0.35, 0.45, 0.60, 0.80, 1.00]


def _ttc_on(sink, vid, t0=None, t1=None):
    out = 0
    for r in sink.records:
        if r.stream != "control.command":
            continue
        if r.payload.get("reason") != "ttc" or r.payload.get("dst") != vid:
            continue
        t = (r.ts_ns - T0_NS) / 1e9
        if t0 is not None and not (t0 <= t <= t1):
            continue
        out += 1
    return out


def one(job):
    amp, seed = job
    scene = default_scene(seed=seed)
    scene.duration_s = 16.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    det = ReferenceDetector(scene)
    sim = Simulation(scene, run_id=f"a3-{amp}-{seed}", out_dir=None, sink=sink,
                     detector=det)
    inst = sim.scheduler.submit(AttackSpec(type="A3", amplitude=amp,
                                           duration=6.0, start=8.0))[0]
    try:
        sim.run()
    finally:
        rules = {a.rule for a in det.alerts}
        has_radar = "radar_range_bias" in rules
        vid = inst.veh_id
        attack_ttc = _ttc_on(sink, vid, 8.0, 14.0)
        injected = "injected" in {r.payload.get("reason")
                                  for r in sink.records if r.stream == "control.command"}
        sim.stop()

    # 同一 seed 的对照（无攻击），用 victim 的 ADAS 制动次数做差
    cscene = default_scene(seed=seed)
    cscene.duration_s = 16.0
    cscene.streams.jsonl = False
    cscene.streams.graph = False
    csink = MemorySink()
    csim = Simulation(cscene, run_id=f"a3c-{amp}-{seed}", out_dir=None, sink=csink)
    try:
        csim.run()
    finally:
        control_ttc = _ttc_on(csink, vid, 8.0, 14.0) if vid else 0
        csim.stop()
    return {
        "amp": amp, "seed": seed,
        "radar": has_radar,
        "ttc_brake": attack_ttc > control_ttc,
        "injected": injected,
        "bias_max": float(inst.bias_max),
        "in_env": bool(inst.truth_event.envelope.get("in_envelope")) if inst.truth_event else False,
        "alerts": sorted(rules),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    jobs = [(amp, seed) for amp in AMPS for seed in range(1, args.seeds + 1)]
    with mp.get_context("spawn").Pool(max(1, args.workers)) as pool:
        rows = pool.map(one, jobs, chunksize=1)
    print(f"{'amp':>5} {'bias':>6} {'radar':>7} {'ttc':>6} {'inject':>7} {'in_env':>7}")
    for amp in AMPS:
        sub = [r for r in rows if r["amp"] == amp]
        n = len(sub)
        def rate(k):
            return f"{sum(1 for r in sub if r[k])}/{n}"
        print(f"{amp:5.2f} {sub[0]['bias_max']:6.2f} {rate('radar'):>7} "
              f"{rate('ttc_brake'):>6} {rate('injected'):>7} {rate('in_env'):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
