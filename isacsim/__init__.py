"""ISAC-SIM：车联网跨层攻击模拟环境。"""

__version__ = "0.2.0"
SCHEMA_VERSION = "isacsim.record/1"
GENERATOR_VERSION = f"isacsim-{__version__}"

# 固定仿真时间零点：2024-11-05T21:40:00Z（东八区 11-06 05:40），便于跨域对齐与复算
T0_NS = 1_730_842_800_000_000_000
