"""攻击阶段指示栏：实时阶段匹配与可点击证据。"""
from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.detect.reference import ReferenceDetector
from isacsim.trace.profiles import StageSpec, TraceProfile


def _sim(secs=8.0, seed=14, trace=True):
    scene = default_scene(seed=seed)
    scene.duration_s = secs
    scene.streams.jsonl = False
    scene.streams.graph = False
    return Simulation(scene, run_id=f"sb-{seed}", detector=ReferenceDetector(scene),
                      trace=trace)


def _run_to(sim, t):
    if sim.sumo is None:
        sim.start()
    while sim.t < t and sim.step_once():
        pass
    return sim.trace.snapshot()


def test_no_attack_has_no_stage_bar():
    sim = _sim(3.0)
    snap = _run_to(sim, 2.0)
    assert snap["stage_bar"] is None
    sim.stop()


def test_a2_stage_bar_matches_ghost_and_is_clickable():
    sim = _sim(6.0)
    spec = AttackSpec(type="A2", amplitude=0.8, duration=3.0, start=2.0)
    sim.scheduler.submit(spec)
    snap = _run_to(sim, 2.6)
    bar = snap["stage_bar"]
    assert bar is not None
    assert bar["type"] == "A2" and bar["state"] == "active"
    by_id = {s["id"]: s for s in bar["stages"]}
    assert by_id["S0"]["status"] == "done"
    assert by_id["S1"]["status"] == "done"
    s1 = by_id["S1"]
    assert s1["evidence"]["target_id"] == "sensor:RSU-01.objects"
    assert "ghost target" in s1["evidence"]["summary"]
    assert s1["t_enter"] is not None and s1["t_enter"] >= 0
    assert bar["current"] in by_id
    sim.stop()


def test_a2_stage_bar_finishes_with_end_stage():
    sim = _sim(7.0)
    sim.scheduler.submit(AttackSpec(type="A2", amplitude=0.8, duration=2.0, start=2.0))
    snap = _run_to(sim, 6.5)
    bar = snap["stage_bar"]
    assert bar["state"] == "finished"
    by_id = {s["id"]: s for s in bar["stages"]}
    assert by_id["END"]["status"] == "done"
    assert by_id["END"]["evidence"]["target_id"].startswith("atk:")
    # 攻击结束后仍有条可回看
    assert snap["stage_bar"] is not None
    sim.stop()


def test_waiting_attack_shows_all_pending():
    sim = _sim(6.0)
    sim.scheduler.submit(AttackSpec(type="A2", amplitude=0.8, duration=2.0, start=5.0))
    snap = _run_to(sim, 1.0)
    bar = snap["stage_bar"]
    assert bar is not None and bar["state"] == "waiting"
    assert all(s["status"] == "pending" for s in bar["stages"])
    sim.stop()


def test_primitive_attack_has_no_bar():
    sim = _sim(4.0)
    sim.scheduler.submit(AttackSpec(type="ghost_object", amplitude=0.8,
                                    duration=1.0, start=1.0))
    snap = _run_to(sim, 3.0)
    assert snap["stage_bar"] is None
    sim.stop()
