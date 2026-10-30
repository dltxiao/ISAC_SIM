from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.layers.health import BUSY, CONGESTED, HEALTHY, compute_health


def test_health_thresholds():
    assert compute_health(load=0.2)["status"] == HEALTHY
    assert compute_health(load=0.7)["status"] == BUSY
    assert compute_health(load=0.9)["status"] == CONGESTED
    assert compute_health(anomaly=True)["status"] == CONGESTED
    assert compute_health(enabled=False)["status"] == "disabled"


def _probe(atype: str | None, until: float = 2.6):
    scene = default_scene(seed=3)
    scene.duration_s = 8.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sim = Simulation(scene, run_id=f"health-{atype}", out_dir=None)
    if atype:
        sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8,
                                        duration=3.0, start=1.0))
    sim.start()
    try:
        while sim.t < until and sim.step_once():
            pass
        snap = sim.snapshot()
        return snap["core"], snap["platform"]
    finally:
        sim.stop()


def test_normal_nodes_are_healthy():
    core, platform = _probe(None, 2.0)
    for key in ("amf", "smf", "upf", "nef", "udm", "ausf", "dn", "gnb"):
        assert core[key]["health"]["status"] == HEALTHY, key
    assert platform["health"]["status"] == HEALTHY


def test_a5_amf_red_platform_busy():
    core, platform = _probe("A5")
    assert core["amf"]["health"]["status"] == CONGESTED
    assert platform["health"]["status"] in (BUSY, CONGESTED)


def test_a6_smf_upf_busy():
    core, _ = _probe("A6")
    assert core["smf"]["health"]["status"] == BUSY
    assert core["upf"]["health"]["status"] == BUSY


def test_a7_upf_dn_red():
    core, platform = _probe("A7")
    assert core["upf"]["health"]["status"] == CONGESTED
    assert core["dn"]["health"]["status"] == CONGESTED
    assert platform["health"]["status"] == CONGESTED


def test_a8_nef_red():
    core, platform = _probe("A8")
    assert core["nef"]["health"]["status"] == CONGESTED
    assert platform["health"]["status"] == CONGESTED


def test_a9_a10_platform_red():
    for atype in ("A9", "A10"):
        core, platform = _probe(atype)
        assert platform["health"]["status"] == CONGESTED, atype
        if atype == "A10":
            assert core["amf"]["health"]["status"] == BUSY
