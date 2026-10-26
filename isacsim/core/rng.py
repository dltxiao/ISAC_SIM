"""确定性随机源：同一 seed + 流名永远得到同一序列。"""
from __future__ import annotations

import zlib

import numpy as np


def _stable_hash(name: str) -> int:
    return zlib.crc32(name.encode("utf-8"))


def rng_for(seed: int, name: str) -> np.random.Generator:
    seq = np.random.SeedSequence([int(seed), _stable_hash(name)])
    return np.random.default_rng(seq)


def gauss(rng: np.random.Generator, sigma: float) -> float:
    return float(rng.normal(0.0, sigma))


def chance(rng: np.random.Generator, p: float) -> bool:
    return bool(rng.random() < p)
