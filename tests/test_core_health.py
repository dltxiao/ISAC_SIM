"""节点健康度必须是“当前状态”，不能因为一次攻击永久变红。

旧实现用累计计数（rejected/received、unsubscribed、route_override 等）算健康度，
A5 风暴后 AMF/SMF 永远红、A7 后 UPF 永远“被劫持”、A8 后 NEF 永远“被退订”。
"""
from isacsim.attacks.base import AttackSpec
from isacsim.batch.benign import BenignOps
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.detect.reference import ReferenceDetector


def _run(atype=None, *, secs=11.0, dur=3.0, start=2.0, marks=(1.0, 2.5, 5.5, 7.5, 10.0), seed=14):
    scene = default_scene(seed=seed)
    scene.duration_s = secs
    scene.streams.jsonl = False
    scene.streams.graph = False
    sim = Simulation(scene, run_id=f"h-{atype}", out_dir=None,
                     detector=ReferenceDetector(scene))
    inst = None
    if atype:
        inst = sim.scheduler.submit(
            AttackSpec(type=atype, amplitude=0.8, duration=dur, start=start))[0]
    sim.start()
    samples = {}
    pending = sorted(marks, reverse=True)
    while sim.step_once():
        while pending and sim.t >= pending[-1]:
            m = pending.pop()
            samples[m] = (sim.core.snapshot(), sim.platform.snapshot())
    samples["end"] = (sim.core.snapshot(), sim.platform.snapshot())
    sim.stop()
    return inst, samples


def _status(snapshot, node):
    return snapshot[node]["health"]["status"]


def test_a5_amf_smf_recover_after_storm():
    _, samples = _run("A5")
    mid_core, _ = samples[2.5]
    assert _status(mid_core, "amf") in ("red", "orange")
    late_core, _ = samples["end"]
    assert _status(late_core, "amf") == "green"
    assert _status(late_core, "smf") == "green"


def test_a7_upf_route_restored_and_healthy():
    sim, samples = _run("A7")
    mid_core, mid_platform = samples[2.5]
    assert _status(mid_core, "upf") == "red"
    assert mid_core["upf"]["route_override"] == "dn:EVIL-01"
    late_core, late_platform = samples["end"]
    assert _status(late_core, "upf") == "green"
    assert late_core["upf"]["route_override"] is None
    assert late_platform["health"]["status"] == "green"


def test_a8_nef_health_is_recent_event_not_lifetime_counter():
    _, samples = _run("A8")
    mid_core, _ = samples[2.5]
    assert _status(mid_core, "nef") == "red"
    late_core, late_platform = samples["end"]
    assert _status(late_core, "nef") == "green"
    assert late_platform["health"]["status"] == "green"


def test_platform_recovers_after_a9_and_a10():
    for atype in ("A9", "A10"):
        _, samples = _run(atype)
        mid_platform = samples[2.5][1]
        assert mid_platform["health"]["status"] in ("red", "orange"), atype
        end_platform = samples["end"][1]
        assert end_platform["health"]["status"] == "green", atype


def test_benign_route_maintenance_ends_healthy():
    scene = default_scene(seed=7)
    scene.duration_s = 9.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sim = Simulation(scene, run_id="benign-health", out_dir=None,
                     detector=ReferenceDetector(scene))
    ops = BenignOps(7, scene, 9.0)
    sim.start()
    while sim.step_once():
        ops.tick(sim.last_ctx)
    core = sim.core.snapshot()
    platform = sim.platform.snapshot()
    sim.stop()
    assert core["upf"]["route_override"] is None
    assert core["upf"]["health"]["status"] == "green"
    assert platform["health"]["status"] == "green"
