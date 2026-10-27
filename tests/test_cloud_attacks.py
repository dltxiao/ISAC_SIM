from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector


def _run(atype: str, seconds: float = 8.0, seed: int = 3):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    det = ReferenceDetector(scene)
    sim = Simulation(scene, run_id=f"cloud-{atype}-{seed}", out_dir=None,
                     sink=sink, detector=det)
    sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8,
                                    duration=3.0, start=1.0))
    sim.run()
    return sim, sink, det


def test_a5_signaling_storm():
    sim, sink, det = _run("A5")
    ev = sim.truth.events[0].to_dict()
    assert {"core", "app", "control"} <= set(ev["domains"])
    streams = {r.stream for r in sink.records}
    assert "core.nas" in streams
    assert "core.session" in streams
    assert "app.ingest" in streams
    assert "control.command" in streams
    assert any(r.payload.get("latency_ms", 0) >= 200
               for r in sink.records if r.stream == "app.ingest")
    assert "nas_registration_flood" in det.snapshot()["by_rule"]


def test_a7_upf_redirect_and_forged_cpm():
    sim, sink, det = _run("A7")
    ev = sim.truth.events[0].to_dict()
    assert {"core", "app", "v2x", "control"} <= set(ev["domains"])
    routes = [r.payload.get("route") for r in sink.records
              if r.stream == "core.upf" and r.payload.get("msg_type") == "route_change"]
    assert "dn:EVIL-01" in routes
    prov = [r.payload.get("provenance") for r in sink.records
            if r.stream == "app.ingest"]
    assert "dn:EVIL-01" in prov
    assert any(r.stream == "v2x.cpm" and r.payload.get("forged") for r in sink.records)
    assert "upf_route_drift" in det.snapshot()["by_rule"]


def test_a9_cloud_control_burst():
    sim, sink, det = _run("A9")
    ev = sim.truth.events[0].to_dict()
    assert {"app", "control", "task", "v2x"} <= set(ev["domains"])
    controls = [r for r in sink.records if r.stream == "app.control"]
    assert controls and controls[0].payload["result"] == "accept"
    assert len(controls[0].payload["vehicle_ids"]) >= 3
    assert any(r.stream == "control.command" for r in sink.records)
    assert "cloud_control_burst" in det.snapshot()["by_rule"]


def test_defense_actions_and_token_revoke():
    scene = default_scene(seed=7)
    scene.duration_s = 5.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id="defense-test", out_dir=None, sink=sink)
    sim.start()
    try:
        for _ in range(8):
            sim.step_once()
        ctx = sim.last_ctx
        sim.core.defense_rate_limit(ctx, 1500)
        sim.core.defense_restore_route(ctx)
        token = next(t for t in sim.platform.iam.tokens.values()
                     if "control:write" in t.scopes)
        sim.platform.revoke_token(ctx, token.token_id)
        actions = [r for r in sink.records if r.stream == "defense.action"]
        assert {a.payload["action"] for a in actions} >= {
            "amf_rate_limit", "upf_route_restore", "platform_revoke_token"}
        # 已吊销 token 不能再下发云控
        vid = sorted(ctx.vehicles)[0]
        before = len([r for r in sink.records if r.stream == "app.control"])
        sim.platform.submit_control(ctx, token_id=token.token_id, vehicle_ids=[vid])
        assert len([r for r in sink.records if r.stream == "app.control"]) > before
        assert [r for r in sink.records if r.stream == "app.control"][-1].payload["result"] == "reject"
    finally:
        sim.stop()
