"""Trace/Watch 数据层的轻量测试。"""
from __future__ import annotations

import threading
import time

import pytest

from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.detect.reference import ReferenceDetector
from isacsim.trace.profiles import PROFILES, profile_for


def _scene(seconds: float = 5.0, seed: int = 14):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    return scene


def test_trace_core_provider_without_attack():
    scene = _scene(4.0, seed=14)
    sim = Simulation(scene, run_id="trace-core", detector=ReferenceDetector(scene),
                     trace=True)
    sim.run()
    snap = sim.trace.snapshot()
    assert snap["providers"] == ["core", "platform"]
    assert snap["last_layer"] == "platform"
    ids = {t["id"] for t in snap["targets"]}
    assert {"core:summary", "core:amf", "core:upf", "core:ues",
            "core:sessions", "core:flows", "platform:summary",
            "platform:tokens", "platform:subscriptions"}.issubset(ids)
    summary = snap["frames"]["core:summary"]["values"]
    assert summary["registered"] > 0
    assert summary["sessions"] > 0
    amf = snap["frames"]["core:amf"]["values"]
    assert amf["health_status"] in ("green", "orange", "red")
    ues = snap["frames"]["core:ues"]["values"]
    assert ues["total"] > 0 and ues["rows"]
    sessions = snap["frames"]["core:sessions"]["values"]
    assert sessions["total"] > 0 and sessions["rows"]
    assert sessions["rows"][0]["session_id"].startswith("sess-")
    flows = snap["frames"]["core:flows"]["values"]
    assert flows["total"] > 0 and flows["rows"]
    assert flows["rows"][0]["flow_id"].startswith("flow-")
    platform = snap["frames"]["platform:summary"]["values"]
    assert platform["health_status"] in ("green", "orange", "red")
    tokens = snap["frames"]["platform:tokens"]["values"]
    assert tokens["total"] > 0 and tokens["rows"]
    assert tokens["rows"][0]["token_id"].startswith("tok-")
    subs = snap["frames"]["platform:subscriptions"]["values"]
    assert subs["total"] > 0 and subs["rows"]
    assert subs["rows"][0]["sub_id"].startswith("sub-")


def test_trace_core_provider_disabled():
    scene = _scene(3.0, seed=5)
    scene.core.enabled = False
    sim = Simulation(scene, run_id="trace-core-off",
                     detector=ReferenceDetector(scene), trace=True)
    sim.run()
    snap = sim.trace.snapshot()
    assert snap["providers"] == ["platform"]
    ids = {t["id"] for t in snap["targets"]}
    assert not any(tid.startswith("core:") for tid in ids)
    assert "platform:summary" in ids


def test_trace_core_events_are_mapped():
    scene = _scene(4.0, seed=14)
    sim = Simulation(scene, run_id="trace-core-events",
                     detector=ReferenceDetector(scene), trace=True)
    sim.run()
    events = list(sim.trace.events)
    nas = [e for e in events if e["kind"] == "core.nas"]
    assert nas
    assert nas[0]["target_id"] == "core:amf"
    assert nas[0]["data"]["stream"] == "core.nas"
    assert nas[0]["data"]["count"] >= 1
    assert any(e["kind"] == "core.session" for e in events)


def test_a5_core_nas_is_aggregated():
    scene = _scene(5.0, seed=14)
    sim = Simulation(scene, run_id="trace-a5-flood",
                     detector=ReferenceDetector(scene), trace=True)
    sim.scheduler.submit(AttackSpec(type="A5", amplitude=0.8,
                                    duration=2.0, start=1.0))
    sim.run()
    nas = [e for e in sim.trace.events if e["kind"] == "core.nas"]
    assert nas
    assert len(nas) < 120
    assert any(e["data"]["count"] > 1 for e in nas)


def test_trace_global_targets_without_attack():
    scene = _scene(3.0, seed=9)
    sim = Simulation(scene, run_id="trace-normal", detector=ReferenceDetector(scene),
                     trace=True)
    sim.run()
    snap = sim.trace.snapshot()
    ids = {t["id"] for t in snap["targets"]}
    assert "sensor:RSU-01.objects" in ids
    assert "veh:fleet" in ids
    assert "net:pc5" in ids
    assert "det:alerts" in ids
    assert snap["state"] == "stopped"
    assert snap["frames"]["net:pc5"]["values"]["cbr"] >= 0.0


def test_trace_cpm_summary_without_attack():
    scene = _scene(4.0, seed=9)
    sim = Simulation(scene, run_id="trace-cpm", detector=ReferenceDetector(scene),
                     trace=True)
    sim.run()
    snap = sim.trace.snapshot()
    ids = {t["id"] for t in snap["targets"]}
    assert "v2x:cpm" in ids
    frame = snap["frames"]["v2x:cpm"]["values"]
    assert frame["rows"] and frame["total"] >= 1
    assert any(r["sender"] == "RSU-01" for r in frame["rows"])
    for row in frame["rows"]:
        assert row["object_count"] >= row["carried"]
        assert row["carried"] <= 12
        assert row["ghost_in_cpm"] == 0
        assert row["ghosts_sensor"] == 0
        assert row["truncated"] == (row["object_count"] > row["carried"])
    # CPM 是 10 Hz 流，不应逐条进事件环
    assert not any(e["kind"] == "v2x.cpm" for e in sim.trace.events)


def test_cpm_ghost_count_uses_sensor_objects():
    scene = _scene(2.0, seed=9)
    sim = Simulation(scene, run_id="trace-cpm-ghost",
                     detector=ReferenceDetector(scene), trace=True)
    svc = sim.trace
    svc.sim.sensing.last_objects["RSU-01"] = [
        {"object_id": 1, "entity": "veh:a"},
        {"object_id": 9000, "entity": "ghost:9000"},
    ]
    assert svc._cpm_ghost_count("RSU-01", [{"object_id": 9000}]) == 1
    assert svc._cpm_ghost_count("RSU-01", [{"object_id": 1}]) == 0
    # 攻击者 CPM 没有对应传感器目标列表
    assert svc._cpm_ghost_count("ATK-A7", [{"object_id": 9000}]) == 0


def test_a7_cpm_summary_marks_forged():
    scene = _scene(5.0, seed=14)
    sim = Simulation(scene, run_id="trace-a7-cpm",
                     detector=ReferenceDetector(scene), trace=True)
    sim.scheduler.submit(AttackSpec(type="A7", amplitude=0.8,
                                    duration=2.0, start=1.0))
    sim.run()
    rows = sim.trace.snapshot()["frames"]["v2x:cpm"]["values"]["rows"]
    forged = [r for r in rows if r["sender"] == "ATK-A7"]
    assert forged and forged[0]["forged"] is True
    assert forged[0]["attack_id"]


def test_a1_trace_has_ghost_event_and_attack_target():
    scene = _scene(6.0, seed=14)
    sim = Simulation(scene, run_id="trace-a1", detector=ReferenceDetector(scene),
                     trace=True)
    sim.scheduler.submit(AttackSpec(type="A1", amplitude=0.6, duration=3.0, start=3.0))
    sim.run()
    snap = sim.trace.snapshot()
    kinds = [e["kind"] for e in snap["events"]]
    assert "attack.start" in kinds
    assert "ghost.added" in kinds
    assert any(t["id"].startswith("atk:") for t in snap["targets"])
    # target RSU 的 ghosts 在攻击期间出现过
    assert any("ghost" in e.get("summary", "") for e in snap["events"])


def test_a5_trace_step_checkpoints():
    scene = _scene(10.0, seed=14)
    sim = Simulation(scene, run_id="trace-a5-step",
                     detector=ReferenceDetector(scene), trace=True)
    th = threading.Thread(target=sim.run, daemon=True)
    th.start()
    try:
        deadline = time.time() + 10
        while sim.state != "running" and time.time() < deadline:
            time.sleep(0.01)
        while sim.t < 3.0 and time.time() < deadline:
            time.sleep(0.01)
        sim.pause("test")
        time.sleep(0.2)
        sim.scheduler.submit(AttackSpec(type="A5", amplitude=0.8,
                                        duration=3.0, start=None))
        reasons = []
        for _ in range(8):
            r = sim.step("checkpoint", timeout=5.0)
            assert r["state"] == "paused"
            step = r.get("step") or {}
            reasons.append(step.get("reason"))
            if step.get("reason") == "S5":
                break
        assert reasons[:6] == ["S0", "S1", "S2", "S3", "S4", "S5"]
        assert step.get("profile_id") == "a5"
    finally:
        sim.request_stop()
        th.join(timeout=5)


def test_trace_step_checkpoint():
    scene = _scene(8.0, seed=14)
    sim = Simulation(scene, run_id="trace-step-test",
                     detector=ReferenceDetector(scene), trace=True)
    th = threading.Thread(target=sim.run, daemon=True)
    th.start()
    try:
        deadline = time.time() + 10
        while sim.state != "running" and time.time() < deadline:
            time.sleep(0.01)
        while sim.t < 2.9 and time.time() < deadline:
            time.sleep(0.01)
        sim.pause("test")
        time.sleep(0.2)
        sim.scheduler.submit(AttackSpec(type="A1", amplitude=0.6,
                                        duration=4.0, start=None))
        r = sim.step("checkpoint", timeout=5.0)
        assert r["state"] == "paused"
        step = r.get("step") or {}
        assert step.get("reason") in ("S1", "S2", "S3", "S4", "S5")
        assert step.get("title")
        assert "to" in step and "from" in step
    finally:
        sim.request_stop()
        th.join(timeout=5)


PROFILE_CASES = [
    ("A2", ("S0", "S1", "S2"), ("ghost.added", "control.command")),
    ("A3", ("S0", "S1", "S2"), ("radar.echo", "radar.track")),
    ("A4", ("S0", "S1", "S2"), ("v2x.cam.clone",)),
    ("A6", ("S0", "S1", "S2"), ("v2x.cpm.forged",)),
    ("A7", ("S0", "S1", "S2"), ("v2x.cpm.forged", "app.ingest")),
    ("A8", ("S0", "S1", "S2"), ("core.nef", "app.subscription")),
    ("A9", ("S0", "S1", "S2"), ("app.auth", "app.control")),
    ("A10", ("S0", "S1", "S2"), ("radar.echo", "core.nas")),
]


def test_profiles_cover_a1_to_a10():
    expected = [f"A{i}" for i in range(1, 11)]
    assert list(PROFILES) == expected
    for atype in expected:
        profile = profile_for(atype)
        assert profile.id == atype.lower()
        assert profile.attack_types == (atype,)
        ids = [stage.id for stage in profile.stages]
        assert len(ids) == len(set(ids))
        assert ids[-1] == "END"
        assert ids[0] == ("S1" if atype == "A1" else "S0")

        seen: set[str] = set()
        for stage in profile.stages:
            assert stage.kinds
            assert stage.pause_at in {
                "after_scheduler", "after_tick",
                "after_layer:core", "after_layer:platform",
            }
            assert stage.wait_s >= 0.0
            for ref in stage.wait_for:
                assert ref in seen
            if stage.wait_ref:
                assert stage.wait_ref in seen
            seen.add(stage.id)
    assert profile_for("NOPE").id == "default"


@pytest.mark.parametrize("atype, expected, required_kinds", PROFILE_CASES)
def test_all_profiles_step_through_checkpoints(atype, expected, required_kinds):
    scene = _scene(8.0, seed=14)
    sim = Simulation(scene, run_id=f"trace-profile-{atype}",
                     detector=ReferenceDetector(scene), trace=True)
    th = threading.Thread(target=sim.run, daemon=True)
    th.start()
    try:
        deadline = time.time() + 10
        while sim.state != "running" and time.time() < deadline:
            time.sleep(0.01)
        while sim.t < 1.0 and time.time() < deadline:
            time.sleep(0.01)
        sim.pause("test")
        time.sleep(0.2)
        sim.scheduler.submit(AttackSpec(type=atype, amplitude=0.8,
                                        duration=3.5, start=None))

        steps = []
        seen_ids = set()
        for _ in range(24):
            result = sim.step("checkpoint", timeout=5.0)
            assert result["state"] in ("paused", "stopped")
            step = result.get("step") or {}
            step_id = step.get("step_id")
            if step_id and step_id not in seen_ids:
                seen_ids.add(step_id)
                steps.append(step)
            if step.get("reason") == "END" or result["state"] == "stopped":
                break

        reasons = [step.get("reason") for step in steps if step.get("reason") != "MAX"]
        assert reasons[:len(expected)] == list(expected)
        assert "END" in reasons
        assert all(step.get("profile_id") == atype.lower() for step in steps)

        kinds = {e["kind"] for e in sim.trace.events}
        assert set(required_kinds).issubset(kinds)

        if atype == "A7":
            assert any(e["kind"] == "app.ingest"
                       and (e.get("data") or {}).get("provenance") == "dn:EVIL-01"
                       for e in sim.trace.events)
        if atype == "A9":
            assert any(e["kind"] == "app.control"
                       and len((e.get("data") or {}).get("vehicle_ids") or []) >= 3
                       for e in sim.trace.events)
    finally:
        sim.request_stop()
        th.join(timeout=5)


def test_radar_events_absent_without_attack():
    scene = _scene(3.0, seed=9)
    sim = Simulation(scene, run_id="trace-normal-radar",
                     detector=ReferenceDetector(scene), trace=True)
    sim.run()
    kinds = {e["kind"] for e in sim.trace.events}
    assert "radar.echo" not in kinds
    assert "radar.track" not in kinds
