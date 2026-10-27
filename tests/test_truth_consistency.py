from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink

DOMAIN_STREAMS = {
    "perception": {"sensing.objects", "sensing.radar.track", "sensing.radar.echo"},
    "radar": {"sensing.radar.echo", "sensing.radar.track"},
    "gnss": {"sensing.gnss"},
    "v2x": {"v2x.cam", "v2x.cpm", "v2x.denm", "v2x.spat"},
    "link": {"link.metrics"},
    "control": {"control.command"},
    "task": {"task.state"},
    "auth": {"auth.cert", "auth.fingerprint"},
    "uu": {"uu.attach", "uu.handover", "uu.session"},
    "core": {"core.nas", "core.session", "core.qos", "core.upf", "core.handover", "core.nef"},
    "app": {"app.auth", "app.ingest", "app.subscription", "app.control", "app.audit"},
}


def _run(atype: str, seconds: float = 8.0, seed: int = 3):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"truth-{atype}-{seed}", out_dir=None, sink=sink)
    sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8, duration=3.0, start=1.0))
    sim.run()
    return sim, {r.stream for r in sink.records}


def test_every_truth_domain_is_observable():
    for atype in ("A1", "A2", "A3", "A4"):
        sim, streams = _run(atype)
        assert sim.truth.events, atype
        ev = sim.truth.events[0].to_dict()
        for domain in ev["domains"]:
            expected = DOMAIN_STREAMS[domain]
            assert expected & streams, f"{atype} domain={domain} 没有对应流；已有 {sorted(streams)}"
