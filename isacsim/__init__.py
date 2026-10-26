"""ISAC-SIM：车联网跨层攻击模拟环境。"""

__version__ = "0.1.0"
SCHEMA_VERSION = "isacsim.record/1"
GENERATOR_VERSION = f"isacsim-{__version__}"

# 固定仿真时间零点：2024-11-06T00:00:00Z 附近，便于跨域对齐与复算
T0_NS = 1_730_842_800_000_000_000
