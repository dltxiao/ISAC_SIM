from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink


def _new_sim(seed: int = 3):
    scene = default_scene(seed=seed)
    scene.duration_s = 6.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"platform-{seed}", out_dir=None, sink=sink)
    return sim, sink


def test_platform_ingests_v2x_messages():
    sim, sink = _new_sim()
    sim.run()
    records = [r for r in sink.records if r.stream == "app.ingest"]
    assert records
    assert all(r.payload["schema_ok"] for r in records)
    assert sim.platform is not None
    snap = sim.snapshot()["platform"]
    assert snap["ingest"]["ingested"] > 0
    assert snap["iam"]["tokens"] >= 2
    assert snap["subscriptions"]["active"] >= 1
    assert any(r.stream == "app.subscription" for r in sink.records)
    assert any(r.stream == "app.auth" for r in sink.records)


def test_platform_control_reaches_control_layer():
    sim, sink = _new_sim()
    sim.start()
    try:
        for _ in range(10):
            sim.step_once()
        ctx = sim.last_ctx
        token = next(t for t in sim.platform.iam.tokens.values()
                     if "control:write" in t.scopes)
        vid = sorted(ctx.vehicles)[0]
        sim.platform.submit_control(
            ctx, token_id=token.token_id, vehicle_ids=[vid],
            command="brake", params={"decel_mps2": 4.0, "duration_s": 1.0},
        )
        for _ in range(3):
            sim.step_once()
        cmds = [r for r in sink.records if r.stream == "control.command"]
        assert cmds
        assert cmds[-1].payload["reason"] == "cloud_control"
        assert cmds[-1].payload["src"] == "platform:V2X-01"
    finally:
        sim.stop()


def test_platform_rejects_bad_token():
    sim, sink = _new_sim(seed=4)
    sim.start()
    try:
        for _ in range(5):
            sim.step_once()
        ctx = sim.last_ctx
        vid = sorted(ctx.vehicles)[0]
        before = len([r for r in sink.records if r.stream == "control.command"])
        sim.platform.submit_control(
            ctx, token_id="tok-does-not-exist", vehicle_ids=[vid],
        )
        for _ in range(3):
            sim.step_once()
        after = len([r for r in sink.records if r.stream == "control.command"])
        assert after == before
        controls = [r for r in sink.records if r.stream == "app.control"]
        assert controls and controls[-1].payload["result"] == "reject"
    finally:
        sim.stop()


def test_nef_token_and_subscription():
    sim, sink = _new_sim(seed=5)
    sim.start()
    try:
        for _ in range(3):
            sim.step_once()
        ctx = sim.last_ctx
        token = sim.core.nef.issue_token(
            ctx, subject="tenant:city-a", tenant="city-a",
            scopes=["nef:subscribe", "nef:notify"],
        )
        sub = sim.core.nef.subscribe(
            ctx, token_id=token.token_id,
            event_types=["denm", "spat"], callback="https://platform.local/nef",
        )
        assert sub is not None
        assert sub.state == "ACTIVE"
        assert sim.core.nef.snapshot()["active_subscriptions"] == 1
        assert any(r.stream == "core.nef" for r in sink.records)
    finally:
        sim.stop()
