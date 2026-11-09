"""A9–A10 回归：token 就绪、批次节奏、幅度映射、真实服务传感器、雷达腿可检性。

对应 2026-11-06 审核里 A9/A10 的条目（R1/R9/R11/R12/R13/R14），以及审核没提的
“A9 周期漂移 + 首拍双发”“A10 雷达腿对所有站同幅度偏置 → 永远不可检”。
"""
import math
from collections import Counter, defaultdict

import pytest

from isacsim import T0_NS
from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector


def _sim(secs=8.0, seed=14):
    scene = default_scene(seed=seed)
    scene.duration_s = secs
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"a910-{seed}", out_dir=None, sink=sink,
                     detector=ReferenceDetector(scene))
    return sim, sink


def _run(atype, *, amp=0.8, start=2.0, dur=3.0, seed=14, secs=8.0):
    sim, sink = _sim(secs, seed)
    inst = sim.scheduler.submit(AttackSpec(
        type=atype, amplitude=amp, duration=dur, start=start))[0]
    sim.start()
    sim.run()
    return sim, inst, sink


def _t(r) -> float:
    return (r.ts_ns - T0_NS) / 1e9


# ---------------------------------------------------------------- A9
def test_a9_immediate_start_has_effect_and_truth():
    sim, inst, sink = _run("A9", start=None, dur=2.0, secs=6.0)
    assert inst.started and not inst.aborted
    ev = sim.truth.events[0]
    assert ev.params["batches"] >= 3
    assert ev.params["commands"] >= 3 * len(ev.params["vehicles"])
    assert any(r.stream == "control.command" for r in sink.records)
    sim.stop()


def test_a9_batches_follow_fixed_cadence_without_double_first():
    sim, inst, sink = _run("A9", dur=3.0)
    times = sorted({round(_t(r), 2) for r in sink.records if r.stream == "app.control"})
    assert len(times) == 6, times           # 3 s / 0.5 s = 6，首拍不再双发
    gaps = [round(b - a, 2) for a, b in zip(times, times[1:])]
    assert all(abs(g - 0.5) <= 1e-6 for g in gaps), gaps
    ev = sim.truth.events[0]
    assert ev.params["batches"] == 6
    assert ev.params["actual_rate"] == pytest.approx(2.0, abs=0.01)
    sim.stop()


def test_a9_amplitude_scales_victims_and_decel():
    low = _run("A9", amp=0.2, dur=2.0, secs=6.0)
    high = _run("A9", amp=0.9, dur=2.0, secs=6.0)
    assert len(low[1].truth_event.params["vehicles"]) < len(high[1].truth_event.params["vehicles"])
    assert low[1].truth_event.params["brake_decel"] < high[1].truth_event.params["brake_decel"]
    assert low[1].truth_event.envelope["in_envelope"] is False
    assert high[1].truth_event.envelope["in_envelope"] is True
    for sim, _, _ in (low, high):
        sim.stop()


def test_a9_honours_target_and_drops_replayed_marker():
    sim, sink = _sim(6.0)
    sim.start()
    while sim.t < 0.5 and sim.step_once():
        pass
    vid = sorted(sim.last_ctx.vehicles)[0]
    inst = sim.scheduler.submit(AttackSpec(
        type="A9", amplitude=0.8, duration=2.0, start=None, target=vid))[0]
    sim.run()
    ev = sim.truth.events[0]
    assert inst.target_matched is True
    assert ev.params["vehicles"][0] == vid
    assert all("replayed" not in (r.payload.get("params") or {})
               for r in sink.records if r.stream == "app.control")
    sim.stop()


def test_a9_cloud_burst_needs_frequency_not_marker():
    sim, inst, sink = _run("A9", dur=2.5)
    rules = Counter(a.rule for a in sim.detector.alerts)
    assert rules["cloud_control_burst"] >= 1
    detail = next(a.detail for a in sim.detector.alerts if a.rule == "cloud_control_burst")
    assert detail["batches_2s"] >= 2 and "replayed" not in detail
    sim.stop()


# ---------------------------------------------------------------- A10
def _victim_frames(sink, vid):
    out = []
    for r in sink.records:
        if r.stream != "sensing.objects":
            continue
        for o in r.payload.get("objects") or []:
            if o.get("entity") == f"veh:{vid}":
                out.append((round(_t(r), 2), r.payload.get("sensor_id"),
                            float(o["x"]), float(o["y"])))
    return out


def test_a10_locks_a_sensor_that_actually_sees_the_victim():
    sim, inst, sink = _run("A10", amp=0.8, dur=3.0)
    ev = sim.truth.events[0]
    assert ev.params["sensor_locked"] is True
    seen = {sid for _, sid, _, _ in _victim_frames(sink, inst.veh_id)}
    assert inst.sensor_id in seen, (inst.sensor_id, seen)
    assert ev.nodes[0] == f"rsu:{inst.sensor_id}"
    sim.stop()


def test_a10_bias_is_single_sensor_and_scales_with_amplitude():
    """只偏服务传感器：攻击窗口内跨站位置差应接近 bias，窗口外接近噪声。"""
    sim, inst, sink = _run("A10", amp=1.0, dur=3.0)
    ev = sim.truth.events[0]
    bias = ev.params["bias_max_m"]
    frames = _victim_frames(sink, inst.veh_id)
    by_t = defaultdict(list)
    for t, sid, x, y in frames:
        by_t[t].append((sid, x, y))
    in_win, out_win = [], []
    for t, pts in by_t.items():
        if len(pts) < 2:
            continue
        spread = max(math.hypot(a[1] - b[1], a[2] - b[2])
                     for i, a in enumerate(pts) for b in pts[i + 1:])
        (in_win if inst.t0 <= t <= inst.t1 else out_win).append(spread)
    assert in_win and max(in_win) > 0.6 * bias, (max(in_win), bias)
    assert not out_win or max(out_win) < 3.0
    sim.stop()


def test_a10_radar_rule_is_reachable_at_max_amplitude():
    sim, inst, sink = _run("A10", amp=1.0, dur=3.0)
    assert "radar_range_bias" in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


def test_a10_default_amplitude_stays_below_radar_threshold():
    sim, inst, sink = _run("A10", amp=0.8, dur=3.0)
    assert inst.bias_max < 6.0
    assert "radar_range_bias" not in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


def test_a10_amplitude_drives_signal_audit_and_brake():
    low = _run("A10", amp=0.1, dur=2.0, secs=6.0)
    high = _run("A10", amp=1.0, dur=2.0, secs=6.0)
    p_low = low[1].truth_event.params
    p_high = high[1].truth_event.params
    assert p_low["signaling_rate"] < p_high["signaling_rate"]
    assert p_low["audit_suppressed"] < p_high["audit_suppressed"]
    assert p_low["brake_decel"] < p_high["brake_decel"]
    assert low[1].truth_event.envelope["in_envelope"] is False
    assert high[1].truth_event.envelope["in_envelope"] is True
    for sim, _, _ in (low, high):
        sim.stop()


def test_a10_cancel_clears_hold_and_hook():
    sim, sink = _sim(6.0)
    inst = sim.scheduler.submit(AttackSpec(type="A10", amplitude=0.8,
                                           duration=4.0, start=1.0))[0]
    sim.start()
    while sim.t < 2.0 and sim.step_once():
        pass
    assert sim.core.amf.hold_queue > 0
    assert sim.hooks._hooks.get("sensing.radar.echo")
    assert sim.cancel_attack(inst.id)
    sim.step_once()
    assert sim.core.amf.hold_queue == 0
    assert not sim.hooks._hooks.get("sensing.radar.echo")
    assert inst.t_end is not None
    sim.stop()
