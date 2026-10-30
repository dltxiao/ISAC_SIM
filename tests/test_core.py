from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.context import HookBus, SimContext
from isacsim.core.engine import Simulation
from isacsim.core.ids import SpanFactory
from isacsim.core.records import MemorySink
from isacsim.core.stats import RunStats


def _run(seconds: float = 4.0, atype: str | None = None, seed: int = 3):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink(limit=200_000)
    sim = Simulation(scene, run_id=f"core-test-{atype}-{seed}", out_dir=None, sink=sink)
    if atype:
        sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8, duration=2.0, start=1.0))
    sim.run()
    return sim, sink


def test_core_registration_and_session():
    sim, sink = _run(seconds=4.0)
    assert sim.core is not None
    streams = {r.stream for r in sink.records}
    assert "core.nas" in streams
    assert "core.session" in streams
    assert "core.qos" in streams
    assert "core.upf" in streams
    assert sim.core.amf.ues
    assert sim.core.smf.sessions
    assert sim.core.upf.flows
    snap = sim.snapshot()["core"]
    assert snap["registered"] > 0
    assert snap["sessions"] > 0


def test_core_events_carry_span():
    _, sink = _run(seconds=4.0)
    core = [r for r in sink.records if r.stream.startswith("core.")]
    assert core
    assert all(r.trace_id and r.span_id for r in core)
    # 同一注册过程至少共享一个 trace_id
    nas = [r for r in core if r.stream == "core.nas"]
    assert nas
    assert len({r.trace_id for r in nas}) >= 1


def test_context_span_is_plumbed_to_record():
    scene = default_scene(seed=7)
    sink = MemorySink()
    ctx = SimContext(
        t=1.0, tick=20, step_s=0.05, run_id="span-test", scene=scene,
        sink=sink, stats=RunStats(), hooks=HookBus(),
        span=SpanFactory().new_trace(chain_id="chain-1"),
    )
    ctx.emit("core.nas", "amf:AMF-01", "amf:AMF-01", {"msg_type": "test"})
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.trace_id and rec.span_id
    assert rec.chain_id == "chain-1"


def test_core_can_be_disabled():
    scene = default_scene(seed=5)
    scene.duration_s = 2.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    scene.core.enabled = False
    sink = MemorySink()
    sim = Simulation(scene, run_id="core-off", out_dir=None, sink=sink)
    sim.run()
    assert sim.core is None
    assert not any(r.stream.startswith("core.") for r in sink.records)


def test_uu_attach_has_gnb():
    _, sink = _run(seconds=3.0)
    attaches = [r for r in sink.records if r.stream == "uu.attach"]
    assert attaches
    assert all(r.payload.get("gnb_id") for r in attaches)


def test_a4_truth_has_no_unimplemented_uu_stage():
    sim, _ = _run(seconds=4.0, atype="A4")
    ev = sim.truth.events[0].to_dict()
    assert "uu" not in ev["domains"]
    assert not any("uu:" in x for x in ev["causal_chain"])
