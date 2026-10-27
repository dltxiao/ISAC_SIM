"""场景配置：数据类 + YAML 载入 + 校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    """配置不合法。"""


@dataclass
class SensorCfg:
    id: str
    kind: str = "rsu"            # rsu / gnb
    x: float = 0.0
    y: float = 0.0
    z: float = 6.0
    yaw_deg: float = 0.0         # 数学约定：0=+x 方向，逆时针为正
    fov_deg: float = 120.0
    range_m: float = 220.0
    rate_hz: float = 10.0
    offset_ms: float = 0.0       # 时钟偏移
    drift_ppm: float = 0.0       # 时钟漂移
    bias_m: float = 0.0          # 标定残差
    coverage_id: str | None = None

    def __post_init__(self) -> None:
        if self.rate_hz <= 0:
            raise ConfigError(f"传感器 {self.id} 的 rate_hz 必须为正")
        if self.range_m <= 0:
            raise ConfigError(f"传感器 {self.id} 的 range_m 必须为正")
        if self.coverage_id is None:
            self.coverage_id = f"{self.id}-COV"


@dataclass
class StreamCfg:
    jsonl: bool = True
    gzip: bool = False
    parquet: bool = False
    graph: bool = True
    rdm: bool = False
    echo: bool = True


@dataclass
class VehicleCfg:
    max: int = 80
    equipped_ratio: float = 0.9
    adas_ratio: float = 0.6


@dataclass
class CoreCfg:
    """语义级核心网配置；不依赖 Open5GS/UERANSIM。"""

    enabled: bool = True
    plmn: str = "00101"
    dnn: str = "v2x"
    snssai: str = "01-000001"
    ip_pool: str = "10.45.0.0/16"
    amf_capacity_tps: float = 5000.0
    smf_capacity_sps: float = 2000.0
    upf_capacity_mbps: float = 10000.0
    auth_capacity_tps: float = 3000.0
    queue_capacity: int = 1000

    def __post_init__(self) -> None:
        if self.amf_capacity_tps <= 0 or self.smf_capacity_sps <= 0:
            raise ConfigError("核心网容量必须为正")
        if self.queue_capacity <= 0:
            raise ConfigError("core.queue_capacity 必须为正")
        if not self.plmn or not self.dnn or not self.snssai:
            raise ConfigError("core 的 plmn/dnn/snssai 不能为空")


@dataclass
class SceneCfg:
    name: str = "arterial"
    seed: int = 11
    duration_s: float = 60.0
    step_s: float = 0.05
    net: str = "assets/nets/arterial/net.net.xml"
    route: str = "assets/routes/arterial.rou.xml"
    sensors: list[SensorCfg] = field(default_factory=list)
    static_objects: list[dict[str, Any]] = field(default_factory=list)
    map: dict[str, Any] = field(default_factory=dict)
    vehicles: VehicleCfg = field(default_factory=VehicleCfg)
    streams: StreamCfg = field(default_factory=StreamCfg)
    core: CoreCfg = field(default_factory=CoreCfg)
    attacks: list[dict[str, Any]] = field(default_factory=list)
    publish: str | None = None    # 例如 udp://127.0.0.1:9001（可选）

    def asset(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    def validate(self) -> None:
        if self.step_s <= 0 or self.step_s > 0.1:
            raise ConfigError("step_s 必须在 (0, 0.1] 内")
        if self.duration_s <= 0:
            raise ConfigError("duration_s 必须为正")
        if not self.sensors:
            raise ConfigError("场景必须至少配置一个传感器")
        for s in self.sensors:
            if s.rate_hz > 1.0 / self.step_s + 1e-9:
                raise ConfigError(
                    f"传感器 {s.id} 的 rate_hz={s.rate_hz} 超过步长允许的 {1 / self.step_s:.0f} Hz"
                )
        if self.vehicles.max <= 0:
            raise ConfigError("vehicles.max 必须为正")


def _sensor_from_dict(d: dict[str, Any]) -> SensorCfg:
    known = {f for f in SensorCfg.__dataclass_fields__}
    unknown = set(d) - known
    if unknown:
        raise ConfigError(f"传感器配置含未知字段：{sorted(unknown)}")
    return SensorCfg(**d)


def load_scene(path: str | Path) -> SceneCfg:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"场景文件不存在：{p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return scene_from_dict(raw)


def scene_from_dict(raw: dict[str, Any]) -> SceneCfg:
    data = dict(raw)
    sensors = [_sensor_from_dict(s) for s in data.pop("sensors", [])]
    vehicles = VehicleCfg(**data.pop("vehicles", {})) if isinstance(data.get("vehicles"), dict) else VehicleCfg()
    streams = StreamCfg(**data.pop("streams", {})) if isinstance(data.get("streams"), dict) else StreamCfg()
    core = CoreCfg(**data.pop("core", {})) if isinstance(data.get("core"), dict) else CoreCfg()
    known = {f for f in SceneCfg.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"场景配置含未知字段：{sorted(unknown)}")
    cfg = SceneCfg(sensors=sensors, vehicles=vehicles, streams=streams, core=core, **data)
    cfg.validate()
    return cfg


def default_scene(seed: int = 11) -> SceneCfg:
    """内置默认场景：400 m 三车道干道 + 1 个信号路口 + 3 RSU + 1 个通感基站。"""
    sensors = [
        SensorCfg(id="RSU-01", kind="rsu", x=60.0, y=6.0, z=6.0, yaw_deg=0.0,
                  fov_deg=120.0, range_m=220.0, rate_hz=10.0, bias_m=0.25),
        SensorCfg(id="RSU-02", kind="rsu", x=200.0, y=6.0, z=6.0, yaw_deg=0.0,
                  fov_deg=120.0, range_m=220.0, rate_hz=10.0, offset_ms=2.5, bias_m=-0.15),
        SensorCfg(id="RSU-03", kind="rsu", x=340.0, y=6.0, z=6.0, yaw_deg=180.0,
                  fov_deg=120.0, range_m=220.0, rate_hz=10.0, offset_ms=1.0),
        SensorCfg(id="GNB-01", kind="gnb", x=200.0, y=-40.0, z=25.0, yaw_deg=30.0,
                  fov_deg=140.0, range_m=400.0, rate_hz=20.0, offset_ms=-1.2, drift_ppm=12.0),
    ]
    return SceneCfg(
        name="arterial",
        seed=seed,
        duration_s=60.0,
        step_s=0.05,
        sensors=sensors,
        static_objects=[
            {"object_id": 500, "cls": "stop_sign", "x": 199.0, "y": 10.0, "z": 1.2},
            {"object_id": 501, "cls": "obstacle", "x": 330.0, "y": -5.5, "z": 0.5},
        ],
        map={"signals": ["C1"], "stop_lines": [{"x": 200.0, "y_low": -6.0, "y_high": 6.0}]},
        vehicles=VehicleCfg(max=80, equipped_ratio=0.9, adas_ratio=0.6),
    )
