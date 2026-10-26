from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.detect.reference import ReferenceDetector


def _run(atype: str | None, seconds: float = 4.0, seed: int = 3):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    det = ReferenceDetector(scene)
    sim = Simulation(scene, run_id=f"smoke-{atype}-{seed}", out_dir=None, detector=det)
    if atype:
        sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8, duration=2.0, start=1.0))
    sim.run()
    return sim, det


def test_smoke_attack_produces_truth_and_alerts():
    sim, det = _run("A4")
    assert len(sim.truth.events) == 1
    assert sim.stats.records_total > 0
    assert det.alerts, det.snapshot()


def test_determinism_same_seed():
    a, _ = _run(None, seconds=3.0, seed=9)
    b, _ = _run(None, seconds=3.0, seed=9)
    assert a.stats.records_total == b.stats.records_total
    assert a.stats.bytes_total == b.stats.bytes_total
    assert [e.to_dict() for e in a.truth.events] == [e.to_dict() for e in b.truth.events]


def test_normal_run_has_no_truth():
    sim, _ = _run(None, seconds=3.0, seed=4)
    assert sim.truth.events == []
