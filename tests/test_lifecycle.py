"""攻击生命周期：取消要真正收尾（摘 hook、停注入、记结束时刻），并发要有上限。"""
from isacsim.attacks.base import AttackSpec
from isacsim.attacks.scheduler import MAX_ACTIVE
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink


def _sim(seed=14, seconds=8.0):
    scene = default_scene(seed=seed)
    scene.duration_s = seconds
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"lifecycle-{seed}", out_dir=None, sink=sink)
    return sim, sink


def _has_ghost(payload: dict) -> bool:
    return any(str(o.get("entity", "")).startswith("ghost:")
               for o in (payload.get("objects") or []))


def test_cancel_detaches_hooks_and_stops_injection():
    sim, sink = _sim()
    inst = sim.scheduler.submit(
        AttackSpec(type="A1", amplitude=0.6, duration=4.0, start=1.0))[0]
    sim.start()
    try:
        while sim.t < 1.5 and sim.step_once():
            pass
        assert inst.started
        assert sim.cancel_attack(inst.id)
        assert not inst.finished, "取消应只登记意图，由下一 tick 收尾"

        sim.step_once()                      # 这一 tick 里执行 stop(ctx)
        assert inst.finished
        assert inst.t_end is not None
        assert len(sim.hooks._hooks.get("sensing.objects", [])) == 0

        before = len(sink.records)
        while sim.t < 2.6 and sim.step_once():
            pass
        ghosts = [r for r in sink.records[before:]
                  if r.stream == "sensing.objects" and _has_ghost(r.payload)]
        assert not ghosts, f"取消后仍有幽灵注入：{len(ghosts)} 帧"
    finally:
        sim.stop()


def test_cancel_is_idempotent_and_reports_state():
    sim, _ = _sim()
    inst = sim.scheduler.submit(
        AttackSpec(type="A2", amplitude=0.6, duration=4.0, start=1.0))[0]
    sim.start()
    try:
        while sim.t < 1.4 and sim.step_once():
            pass
        assert sim.cancel_attack(inst.id)
        assert not sim.cancel_attack(inst.id), "重复取消应返回 False"
        sim.step_once()
        snap = {s["id"]: s for s in sim.scheduler.snapshot()}
        assert snap[inst.id]["cancelled"] is True
        assert snap[inst.id]["finished"] is True
        # 再次 step 不应再触发收尾逻辑
        sim.step_once()
        assert inst.finished
    finally:
        sim.stop()


def test_scheduler_caps_concurrent_attacks():
    sim, _ = _sim(seconds=3.0)
    sim.start()
    try:
        sim.scheduler.submit(AttackSpec(type="A1", count=20, amplitude=0.2,
                                        duration=1.0, start=0.5))
        sim.scheduler.submit(AttackSpec(type="A1", count=20, amplitude=0.2,
                                        duration=1.0, start=0.5))
        assert sum(1 for i in sim.scheduler.instances if not i.finished) == MAX_ACTIVE
        try:
            sim.scheduler.submit(AttackSpec(type="A1", count=1, amplitude=0.2,
                                            duration=1.0, start=0.5))
        except ValueError as exc:
            assert "上限" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("超过 MAX_ACTIVE 应被拒绝")
    finally:
        sim.stop()


def test_truth_observed_records_actual_end_and_cancel(tmp_path):
    """R17：t1/stages 是计划，observed 记录实际结束时刻与取消状态。"""
    import json

    scene = default_scene(seed=14)
    scene.duration_s = 8.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sim = Simulation(scene, run_id="lifecycle-obs", out_dir=tmp_path)
    inst = sim.scheduler.submit(
        AttackSpec(type="A2", amplitude=0.6, duration=5.0, start=1.0))[0]
    sim.start()
    try:
        while sim.t < 1.6 and sim.step_once():
            pass
        assert sim.cancel_attack(inst.id)
        sim.run()
    finally:
        sim.stop()

    events = [json.loads(line) for line in
              (tmp_path / "truth" / "events.jsonl").read_text().splitlines() if line.strip()]
    ev = events[0]
    assert ev["t1"] == 6.0, "t1 仍是计划结束时刻"
    assert ev["observed"]["cancelled"] is True
    assert ev["observed"]["t_end"] < ev["t1"]
    assert "evidence_seen" in ev["observed"] and "evidence_missing" in ev["observed"]
