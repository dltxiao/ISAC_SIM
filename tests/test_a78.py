"""A7–A8 回归：真实溯源改向、退订范围、首拍不双发、二次运行仍有效。

对应 2026-11-06 审核里 A7/A8 的条目（R1/R9/R12/R13/R16），以及审核没提的
“A8 第二次运行静默无效”“A8 人造延迟”两个问题。
"""
from collections import Counter

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
    sim = Simulation(scene, run_id=f"a78-{seed}", out_dir=None, sink=sink,
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


# ---------------------------------------------------------------- A7
def test_a7_first_injection_is_single():
    """首拍不能双发：3 s、4.2 Hz 应约 13 条，不是旧实现的“双发 + 0.5 s”。"""
    sim, inst, sink = _run("A7", amp=0.8, dur=3.0)
    cams = [r for r in sink.records
            if r.stream == "v2x.cpm" and r.source == "ATK-A7"]
    assert 10 <= len(cams) <= 16, len(cams)
    assert len(cams) == inst.truth_event.params["injected"]
    # 第一条不应与第二条挤在同一 tick
    assert _t(cams[1]) - _t(cams[0]) >= 0.1
    sim.stop()


def test_a7_route_change_rewrites_real_ingest_provenance():
    """路由改向必须影响真实流量，而不只是那几条伪造 CPM。"""
    sim, inst, sink = _run("A7", amp=0.8, dur=3.0)
    rows = [(_t(r), r.payload.get("msg_type"), r.payload.get("provenance"),
             r.payload.get("source"))
            for r in sink.records if r.stream == "app.ingest"]
    evil = [x for x in rows if x[2] == "dn:EVIL-01"]
    forged = [x for x in evil if x[3] == "ATK-A7"]
    real = [x for x in evil if x[3] != "ATK-A7"]
    assert len(real) >= 20, "真实 CPM/SPaT 也应带异常来源"
    assert any(x[1] == "spat" for x in real)
    # 攻击窗口之外回到 dn:DN-01
    # 首拍与恢复同拍都有浮点误差，窗口外留 0.06 s 余量
    assert all(x[2] == "dn:DN-01" for x in rows
               if x[0] < inst.t0 - 0.06 or x[0] > inst.t1 + 0.06)
    assert len(forged) == len([r for r in sink.records
                               if r.stream == "v2x.cpm" and r.source == "ATK-A7"])
    assert "ingest_provenance_mismatch" in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


def test_a7_amplitude_drives_rate_and_brake():
    low = _run("A7", amp=0.2, dur=2.0, secs=6.0)
    high = _run("A7", amp=1.0, dur=2.0, secs=6.0)
    p_low = low[1].truth_event.params
    p_high = high[1].truth_event.params
    assert p_low["inject_rate"] < p_high["inject_rate"]
    assert p_low["injected"] < p_high["injected"]
    assert p_low["brake_decel"] < p_high["brake_decel"]
    assert low[1].truth_event.envelope["in_envelope"] is False
    assert high[1].truth_event.envelope["in_envelope"] is True
    for sim, _, _ in (low, high):
        sim.stop()


def test_a7_cancel_restores_route():
    sim, sink = _sim(6.0)
    inst = sim.scheduler.submit(AttackSpec(type="A7", amplitude=0.8,
                                           duration=4.0, start=1.0))[0]
    sim.start()
    while sim.t < 2.0 and sim.step_once():
        pass
    assert sim.core.upf.route_override == "dn:EVIL-01"
    assert sim.cancel_attack(inst.id)
    sim.step_once()
    # 取消后必须真正清掉 override（旧实现只改成 "dn:DN-01"，健康度仍判红）
    assert sim.core.upf.route_override is None
    assert sim.core.upf.snapshot()["route"] == "dn:DN-01"
    assert inst.t_end is not None
    sim.stop()


# ---------------------------------------------------------------- A8
def test_a8_immediate_start_no_longer_half_runs():
    """立即启动也要真的撤到平台订阅（ready 等 bootstrap）。"""
    sim, inst, sink = _run("A8", amp=0.8, start=None, dur=2.0, secs=6.0)
    drops = [r for r in sink.records
             if r.stream == "app.ingest" and r.payload.get("cause") == "no_subscription"]
    assert len(drops) >= 20, len(drops)
    assert not inst.aborted
    assert "ingest_drop_spike" in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


def test_a8_amplitude_drives_unsubscribe_scope():
    low = _run("A8", amp=0.3, dur=2.0, secs=6.0)
    high = _run("A8", amp=0.9, dur=2.0, secs=6.0)
    assert low[1].truth_event.params["revoked_event_types"] == ["denm"]
    assert len(high[1].truth_event.params["revoked_event_types"]) == 3
    assert low[1].truth_event.envelope["in_envelope"] is False
    assert high[1].truth_event.envelope["in_envelope"] is True
    drops_low = sum(1 for r in low[2].records
                    if r.stream == "app.ingest" and r.payload.get("cause") == "no_subscription")
    drops_high = sum(1 for r in high[2].records
                     if r.stream == "app.ingest" and r.payload.get("cause") == "no_subscription")
    assert drops_high > drops_low * 5
    assert low[1].truth_event.params["brake_decel"] < high[1].truth_event.params["brake_decel"]
    for sim, _, _ in (low, high):
        sim.stop()


def test_a8_second_run_still_drops_events():
    """第二次注入不能撤到一个已 REVOKED 的订阅（旧实现 0 条 dropped）。"""
    sim, sink = _sim(9.0)
    first = sim.scheduler.submit(AttackSpec(type="A8", amplitude=0.8,
                                            duration=2.0, start=1.0))[0]
    second = sim.scheduler.submit(AttackSpec(type="A8", amplitude=0.8,
                                             duration=2.0, start=4.5))[0]
    sim.start()
    sim.run()
    drops = [_t(r) for r in sink.records
             if r.stream == "app.ingest" and r.payload.get("cause") == "no_subscription"]
    first_win = [t for t in drops if first.t0 <= t <= first.t1]
    second_win = [t for t in drops if second.t0 <= t <= second.t1]
    assert first_win and second_win, (len(first_win), len(second_win))
    assert any(s.state == "ACTIVE" and "denm" in s.event_types
               for s in sim.platform.subs.subs.values())
    sim.stop()


def test_a8_no_artificial_ingest_delay():
    """A8 不再靠人造 80 ms 延迟触发 ingest_latency_spike。"""
    sim, inst, sink = _run("A8", amp=0.8, dur=2.0, secs=6.0)
    lat = [r.payload.get("latency_ms") for r in sink.records if r.stream == "app.ingest"]
    assert lat and max(lat) < 50.0
    assert "ingest_latency_spike" not in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


def test_a8_nef_leg_declared_as_no_effect():
    sim, inst, _ = _run("A8", amp=0.8, dur=2.0, secs=6.0)
    assert inst.truth_event.params["nef_leg_effect"] == "none"
    assert "core.nef" in inst.truth_event.expected_evidence
    sim.stop()
