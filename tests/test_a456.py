"""A4–A6 的“真影响”回归：速率、幅度、状态机传导、检测去标记。

这些测试针对 2026-11-06 审核里 A4–A6 的条目（R1/R4/R9/R10/R11/R16/R17）：
不只看“注入没报错”，而是看发起强度是否等于真值、后果是否由上游状态机产生。
"""
from collections import Counter

import pytest

from isacsim import T0_NS
from isacsim.attacks.base import AttackSpec, Pacer
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector
from isacsim.layers.common import is_equipped


def _run(atype, *, amp=0.8, start=1.0, dur=2.0, seed=14, secs=6.0, target=None):
    scene = default_scene(seed=seed)
    scene.duration_s = secs
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"a456-{atype}-{seed}", out_dir=None, sink=sink,
                     detector=ReferenceDetector(scene))
    inst = sim.scheduler.submit(AttackSpec(
        type=atype, amplitude=amp, duration=dur, start=start, target=target))[0]
    sim.start()
    sim.run()
    return sim, inst, sink


def _t(r) -> float:
    return (r.ts_ns - T0_NS) / 1e9


# ---------------------------------------------------------------- Pacer
def test_pacer_keeps_average_rate():
    """浮点门控会把 16.4 Hz 量化成 10 Hz；补发式节奏要保持平均速率。"""
    p = Pacer(16.4)
    sent = 0
    t = 0.0
    for _ in range(61):          # 3 s @ 0.05 s
        sent += p.due(t, max_burst=2)
        t = round(t + 0.05, 6)
    assert 45 <= sent <= 52, sent      # ≈ 16.4 × 3
    assert 13.0 <= p.actual_hz(3.0) <= 18.0


# ---------------------------------------------------------------- A4
def test_a4_actual_rate_matches_truth():
    sim, inst, sink = _run("A4", amp=0.8, start=1.0, dur=3.0, secs=6.0)
    cams = [r for r in sink.records
            if r.stream == "v2x.cam" and r.source == f"ATK-{inst.victim}"]
    assert cams, "A4 应发出伪 CAM"
    span = _t(cams[-1]) - _t(cams[0])
    measured = (len(cams) - 1) / span
    assert abs(measured - inst.spoof_rate) <= 1.0, (measured, inst.spoof_rate)
    ev = sim.truth.events[0]
    assert abs(ev.params["actual_rate"] - measured) <= 1.0
    # 载荷里的 rate_hz 必须与名义速率一致，不能写死 10
    assert cams[0].payload["rate_hz"] == pytest.approx(inst.spoof_rate, abs=0.2)
    sim.stop()


def test_a4_amplitude_moves_offset_and_envelope():
    sim, inst, _ = _run("A4", amp=0.2, start=1.0, dur=2.0, secs=5.0)
    low = (inst.offset_m, inst.spoof_rate, sim.truth.events[0].envelope["in_envelope"])
    sim.stop()
    sim2, inst2, _ = _run("A4", amp=0.9, start=1.0, dur=2.0, secs=5.0)
    high = (inst2.offset_m, inst2.spoof_rate, sim2.truth.events[0].envelope["in_envelope"])
    sim2.stop()
    assert low[0] < high[0] and low[1] < high[1]
    assert low[2] is False and high[2] is True


def test_a4_honours_target():
    scene = default_scene(seed=14)
    scene.duration_s = 5.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sim = Simulation(scene, run_id="a4-target", out_dir=None, sink=MemorySink())
    sim.start()
    while sim.t < 0.5 and sim.step_once():
        pass
    vid = next(v for v in sorted(sim.last_ctx.vehicles)
               if is_equipped(v, scene.vehicles.equipped_ratio))
    inst = sim.scheduler.submit(AttackSpec(
        type="A4", amplitude=0.8, duration=2.0, start=None, target=vid))[0]
    sim.run()
    assert inst.victim == vid
    ev = sim.truth.events[0]
    assert ev.params["target_matched"] is True
    sim.stop()


# ---------------------------------------------------------------- A5
def test_a5_flood_goes_through_amf_state():
    sim, inst, sink = _run("A5", amp=0.8, start=1.0, dur=2.5, secs=6.0)
    amf = sim.core.amf
    # 真消息：请求数 = 实际发送数，且被容量挡下一部分
    reqs = [r for r in sink.records
            if r.stream == "core.nas" and r.payload.get("msg_type") == "registration_request"]
    assert len(reqs) > 500
    assert amf.received > 500 and amf.rejected > 0
    assert amf.dropped == amf.rejected
    ev = sim.truth.events[0]
    assert ev.params["actual_rate"] > 0.5 * ev.params["rate"]
    # 攻击结束后队列排空、合成 UE 清理干净
    assert amf.queue_depth < 50
    assert not [u for u in sim.core.amf.ues if u.startswith("imsi-")]
    assert not [u for u in sim.core.smf.sessions if u.startswith("imsi-")]
    sim.stop()


def test_a5_victim_reregistration_is_rejected_then_recovers():
    sim, inst, sink = _run("A5", amp=0.8, start=1.0, dur=2.5, secs=7.0)
    vid = inst.veh_id
    rows = [( _t(r), r.payload.get("msg_type"), r.payload.get("result"),
              r.payload.get("cause"))
            for r in sink.records
            if r.stream == "core.nas" and r.payload.get("ue_id") == vid]
    rejects = [x for x in rows if x[2] == "reject"]
    assert rejects, "受害车重注册应被 AMF 真拒绝"
    assert rejects[0][3] == "congestion"
    # 攻击窗口内被拒，窗口外恢复入网并重建会话
    t1 = inst.t_end
    assert rejects[0][0] < t1
    accepts_after = [x for x in rows if x[2] == "accept" and x[0] >= t1]
    assert accepts_after, "攻击结束后受害车应重新注册成功"
    sessions = [r for r in sink.records
                if r.stream == "core.session" and r.payload.get("ue_id") == vid
                and r.payload.get("result") == "accept" and _t(r) >= t1]
    assert sessions, "攻击结束后应重新建立 PDU 会话"
    sim.stop()


def test_a5_detection_does_not_need_bot_marker():
    sim, inst, sink = _run("A5", amp=0.8, start=1.0, dur=2.5, secs=6.0)
    assert not any(r.payload.get("bot") for r in sink.records
                   if r.stream in ("core.nas", "core.session")), "不应再有自报 bot 标记"
    rules = Counter(a.rule for a in sim.detector.alerts)
    assert rules["nas_registration_flood"] >= 1
    sim.stop()


# ---------------------------------------------------------------- A6
def test_a6_waits_for_session_instead_of_silently_finishing():
    sim, inst, _ = _run("A6", amp=0.8, start=None, dur=2.0, secs=5.0)
    assert inst.started and not inst.aborted
    assert inst.t0 >= 0.1, "应等首个 PDU 会话建立后再开始"
    ev = sim.truth.events[0]
    assert ev.params["victim"]
    sim.stop()


def test_a6_amplitude_drives_qos_and_consequences():
    sim, inst, sink = _run("A6", amp=0.8, start=1.0, dur=2.0, secs=5.0)
    ev = sim.truth.events[0]
    assert ev.params["5qi_to"] == 9 and ev.params["gfbr_to"] < 500
    assert ev.params["ingest_delay_ms"] >= 100
    assert ev.envelope["in_envelope"] is True
    # 攻击结束恢复原值（79 而不是硬写 9）
    qos = sim.core.smf.sessions[inst.veh_id].qos_flows[1]
    assert qos.five_qi == inst.old_5qi == 79
    assert qos.gfbr_kbps == inst.old_gfbr
    mods = [(r.payload.get("5qi"), r.payload.get("old_5qi"))
            for r in sink.records if r.stream == "core.qos"]
    assert (9, 79) in mods and (79, 9) in mods
    sim.stop()


def test_a6_cpm_reception_degrades_for_victim():
    sim, inst, sink = _run("A6", amp=0.8, start=1.0, dur=2.0, secs=5.0)
    rx = [r for r in sink.records
          if r.stream == "v2x.cpm.rx" and r.payload.get("receiver") == inst.veh_id]
    assert len(rx) >= 10
    during = [r for r in rx if inst.t0 <= _t(r) <= (inst.t_end or 1e9)]
    assert during and all(r.payload["5qi"] == 9 for r in during)
    loss_ratio = sum(1 for r in during if r.payload["lost"]) / len(during)
    expect = sim.truth.events[0].params["cpm_loss_p"]
    assert abs(loss_ratio - expect) <= 0.35, (loss_ratio, expect)
    # 降级只发生在受害车：其他车的接收仍按 79 档
    others = [r for r in rx if r is not during[0]]
    assert any(r.payload["5qi"] != 9 for r in others)
    assert "cpm_degraded" in Counter(a.rule for a in sim.detector.alerts)
    sim.stop()


# ---------------------------------------------------------------- observed
def test_observed_counts_evidence_emitted_inside_start():
    """start() 里发出的证据也要算"出现过"（旧实现基线取晚了，会误报缺失）。"""
    sim, inst, _ = _run("A4", amp=0.8, start=1.0, dur=2.0, secs=4.0)
    obs = sim.truth.events[0].observed
    assert "auth.cert" in obs["evidence_seen"]
    assert obs["evidence_missing"] == []
    sim.stop()
