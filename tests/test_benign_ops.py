"""合法运维样本（R5 难负样本）：动作要真的发生，且不能被当成攻击。

这组测试跑一次完整的正常场景（含 9 个合法运维动作），断言：
1. 动作确实执行（meta 里可追溯）；
2. 不产生任何告警（QoS 调整、N6 割接、单类型退订、token 轮换、单批车队控制、真实急刹）；
3. 真实急刹仍然产生 DENM（不是“把正常行为也静音”）。
"""
from isacsim import T0_NS
from isacsim.batch.benign import BenignOps
from isacsim.batch.runner import build_jobs
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector


def _run_normal(seed=7, duration=8.0):
    scene = default_scene(seed=seed)
    scene.duration_s = duration
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"benign-{seed}", out_dir=None, sink=sink,
                     detector=ReferenceDetector(scene))
    ops = BenignOps(seed, scene, duration)
    sim.start()
    try:
        while sim.step_once():
            ops.tick(sim.last_ctx)
    finally:
        sim.stop()
    return sim, ops, sink


def test_benign_ops_run_and_raise_no_alerts():
    sim, ops, sink = _run_normal()
    names = {a["op"] for a in ops.actions}
    assert {"qos_change", "qos_restore", "route_switch", "route_restore",
            "unsubscribe_spat", "resubscribe_spat", "token_rotate",
            "fleet_control", "real_brake"} <= names
    rules = [a.rule for a in sim.detector.alerts]
    assert rules == [], f"合法运维被误报：{rules}"


def test_real_brake_still_produces_denm():
    sim, ops, sink = _run_normal()
    denms = [r for r in sink.records if r.stream == "v2x.denm"]
    assert denms, "真实急刹应产生 DENM"
    assert any(r.payload.get("cause") == "emergency_brake" for r in denms)


def test_route_maintenance_does_not_flag_provenance():
    sim, ops, sink = _run_normal()
    prov = {r.payload.get("provenance") for r in sink.records if r.stream == "app.ingest"}
    assert "dn:DN-02" in prov, "割接期间的 ingest 应带 DN-02"
    assert "ingest_provenance_mismatch" not in [a.rule for a in sim.detector.alerts]


def test_legit_qos_change_keeps_cpm_quality():
    """合法切片调整（79→8、800 kbps）只掉 ~15%，不应被判成降级。"""
    sim, ops, sink = _run_normal()
    rx = [r for r in sink.records
          if r.stream == "v2x.cpm.rx" and r.payload.get("5qi") == 8]
    assert rx, "合法 QoS 调整后应有 5qi=8 的接收记录"
    lost = sum(1 for r in rx if r.payload.get("lost"))
    assert lost / len(rx) < 0.30
    assert "cpm_degraded" not in [a.rule for a in sim.detector.alerts]


def test_batch_jobs_pair_seeds_and_allow_disabling_ops():
    jobs = build_jobs("x", attacks=3, normal=3, types=["A1", "A2"], duration=10,
                      amplitude=0.8, split="t", streams=False, seed0=7)
    atk = [j for j in jobs if j.kind == "attack"]
    nor = [j for j in jobs if j.kind == "normal"]
    assert [j.seed for j in atk] == [7, 8, 9]
    assert [j.seed for j in nor] == [7, 8, 9]      # 同段 seed 配对
    assert all(j.ops for j in nor)
    off = build_jobs("x", attacks=1, normal=1, types=["A1"], duration=10,
                     amplitude=0.8, split="t", streams=False, seed0=7, ops=False)
    assert [j.ops for j in off if j.kind == "normal"] == [False]
