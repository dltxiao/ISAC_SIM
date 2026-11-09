"""A3 跨层隐蔽攻击：雷达回波渐偏 → 目标列表偏移 → ADAS 误判 → 伪指令 + 掩盖。

测试覆盖“真影响”的每一环，而不只是“注入没报错”：
1. 选中的是“受害车 + 前车 + 服务传感器”，且受害车不是前车；
2. 回波的距离与方位被改写 → 目标列表里的位置被拉近；
3. ADAS 因被篡改的 TTC 触发一次本不该有的制动（对照组不触发）；
4. 60% 处注入一条幅度随偏移递增的伪指令；
5. 回波被标记 masked 且 SNR 下降；
6. 检测侧能给出 radar_range_bias。
"""
import math

from isacsim.attacks.base import AttackSpec
from isacsim.config import default_scene
from isacsim.core.engine import Simulation
from isacsim.core.records import MemorySink
from isacsim.detect.reference import ReferenceDetector


def _run(seed: int = 12, start: float = 8.0, duration: float = 6.0,
         amplitude: float = 0.8, attack: bool = True):
    scene = default_scene(seed=seed)
    scene.duration_s = start + duration + 8.0
    scene.streams.jsonl = False
    scene.streams.graph = False
    sink = MemorySink()
    sim = Simulation(scene, run_id=f"a3-test-{seed}-{int(attack)}", out_dir=None,
                     sink=sink, detector=ReferenceDetector(scene))
    inst = None
    if attack:
        inst = sim.scheduler.submit(
            AttackSpec(type="A3", amplitude=amplitude, duration=duration, start=start))[0]
    sim.start()
    sim.run()
    return sim, inst, sink


def test_a3_locks_victim_and_lead():
    sim, inst, _ = _run()
    assert inst.veh_id and inst.lead_id
    assert inst.veh_id != inst.lead_id
    assert inst.sensor_id in {s.id for s in sim.scene.sensors}
    assert inst.bias_max > 3.0
    ev = sim.truth.events[0]
    assert ev.params["victim"] == inst.veh_id
    assert ev.params["lead"] == inst.lead_id
    assert ev.params["spoof"] == "range+azimuth"


def test_a3_echo_is_spoofed_and_object_moves_closer():
    """回波被改 → 点迹残差变大 → 目标列表里的位置比真实位置更近。"""
    sim, inst, sink = _run()
    echoes = [r.payload for r in sink.records
              if r.stream == "sensing.radar.echo" and r.entity == f"veh:{inst.lead_id}"]
    assert echoes, "没有捕获到前车回波"
    assert any(e.get("masked") for e in echoes), "回波没有被标记为掩盖"
    assert min(e["snr_db"] for e in echoes) < 30.0

    # 单条 track 内已经“自洽”（pos 由同一条被改回波重建），所以要用跨站点互证：
    # 同一 target 在不同传感器上的位置差，就是被篡改的那一站与其他站的偏差。
    tracks = [r for r in sink.records
              if r.stream == "sensing.radar.track" and r.entity == f"veh:{inst.lead_id}"]
    by_frame: dict[int, list[dict]] = {}
    for r in tracks:
        # 不同传感器的 frame_seq 各自计数，按 0.1 s 时间桶对齐才可靠
        by_frame.setdefault(int(round(r.ts_ns / 1e8)), []).append(r.payload)
    worst = 0.0
    for rows in by_frame.values():
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                d = math.hypot(a["pos_x"] - b["pos_x"], a["pos_y"] - b["pos_y"])
                worst = max(worst, d)
    assert worst > 6.0, f"跨站点位置没有被明显拉开（max spread={worst:.2f} m）"


def test_a3_adas_brakes_on_spoofed_ttc():
    """因果对照：同一 seed/时刻，只有带攻击时才出现受害车自己的 TTC 制动。"""
    sim, inst, sink = _run()
    cmds = [r.payload for r in sink.records if r.stream == "control.command"]
    adas = [c for c in cmds if c.get("dst") == inst.veh_id and c.get("reason") == "ttc"]
    assert adas, "受害车没有因被篡改的 TTC 触发 ADAS 制动"
    assert min(c["params"]["ttc_s"] for c in adas) < 2.5

    _sim0, _inst0, sink0 = _run(attack=False)
    cmds0 = [r.payload for r in sink0.records if r.stream == "control.command"]
    adas0 = [c for c in cmds0 if c.get("dst") == inst.veh_id and c.get("reason") == "ttc"]
    assert not adas0, "对照组也刹了车，说明这条链不是攻击造成的"


def test_a3_injected_command_scales_with_bias():
    sim, inst, sink = _run()
    cmds = [r.payload for r in sink.records if r.stream == "control.command"]
    inj = [c for c in cmds if c.get("dst") == inst.veh_id and c.get("reason") == "injected"]
    assert len(inj) == 1
    cmd = inj[0]
    assert cmd["params"]["ttc_s"] == 99.0
    assert cmd["src"] == f"attacker:ctl-{inst.veh_id}"
    # 60% 进度注入：1.0 + 1.5×0.6 = 1.9 m/s²，且必须落在 A3 的判别区间内
    assert 1.5 <= cmd["params"]["decel_mps2"] <= 2.5


def test_a3_emits_telemetry_mask():
    sim, inst, sink = _run()
    masks = [r.payload for r in sink.records if r.stream == "telemetry.mask"]
    assert len(masks) == 1
    assert masks[0]["target"] == inst.lead_id
    assert masks[0]["masked_echoes"] > 0
    assert masks[0]["snr_drop_db"] > 0


def test_a3_detector_flags_radar_range_bias():
    sim, inst, _ = _run()
    rules = [a.rule for a in sim.detector.alerts]
    assert "radar_range_bias" in rules
    # A3 不往目标列表里塞幽灵，ghost_object 不应被误触发
    assert "ghost_object" not in rules
