import math
from isacsim.world.geometry import (heading_to_vec, in_fov, occluded,
                                    sumo_angle_to_heading, wrap180)


def test_heading_conversion():
    assert sumo_angle_to_heading(0) == 90          # 北 -> 数学 90°
    assert abs(sumo_angle_to_heading(90)) < 1e-9   # 东 -> 0°
    hx, hy = heading_to_vec(0)
    assert abs(hx - 1) < 1e-9 and abs(hy) < 1e-9


def test_wrap180_and_fov():
    assert wrap180(190) == -170
    assert in_fov(0, 0, 120)
    assert not in_fov(90, 0, 120)


def test_occlusion_by_truck():
    # 传感器在 (0,0)，目标在 (50,0)，卡车挡在中间
    assert occluded(0, 0, 50, 0, [(25, 0, 9.0, 2.4, 0.0)])
    # 卡车在远处，不挡
    assert not occluded(0, 0, 50, 0, [(25, 20, 9.0, 2.4, 0.0)])
