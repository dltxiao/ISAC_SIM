import pytest
from isacsim.config import ConfigError, SensorCfg, default_scene, scene_from_dict


def test_default_scene_valid():
    s = default_scene()
    assert len(s.sensors) >= 3
    assert s.step_s <= 0.1


def test_unknown_field_rejected():
    with pytest.raises(ConfigError):
        scene_from_dict({"name": "x", "bogus": 1, "sensors": [{"id": "R1"}]})


def test_sensor_rate_over_step_rejected():
    with pytest.raises(ConfigError):
        scene_from_dict({"step_s": 0.1, "sensors": [{"id": "R1", "rate_hz": 20}]})


def test_sensor_defaults():
    s = SensorCfg(id="R1")
    assert s.coverage_id == "R1-COV"
