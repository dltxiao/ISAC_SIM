"""Trace/Watch 数据层的轻量测试。"""
from __future__ import annotations

import threading
import time

from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.detect.reference import ReferenceDetector


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
            "platform:summary"}.issubset(ids)
    summary = snap["frames"]["core:summary"]["values"]
    assert summary["registered"] > 0
    assert summary["sessions"] > 0
    amf = snap["frames"]["core:amf"]["values"]
    assert amf["health_status"] in ("green", "orange", "red")
    ues = snap["frames"]["core:ues"]["values"]
    assert ues["total"] > 0 and ues["rows"]
    platform = snap["frames"]["platform:summary"]["values"]
    assert platform["health_status"] in ("green", "orange", "red")


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
