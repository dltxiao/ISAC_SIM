from isacsim.config import default_scene
from isacsim.core.preflight import check_scene, estimate_scene, hardware_info


def test_estimate_positive():
    est = estimate_scene(default_scene())
    assert est.records > 0 and est.wall_s > 0 and est.mem_mb > 0


def test_default_scene_within_budget():
    assert check_scene(default_scene()).ok


def test_over_limit_rejected():
    s = default_scene()
    s.vehicles.max = 5000
    assert not check_scene(s).ok


def test_hardware_info():
    hw = hardware_info()
    assert hw["cpu_count"] >= 1 and hw["mem_total_gib"] > 0
