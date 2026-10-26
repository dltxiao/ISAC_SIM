"""认证层：假名证书与信道指纹（CFO / IQ 失衡 / RSSI）。

指纹由设备固有偏差决定（同设备稳定、不同设备可分），
A4 双因素仿冒的载体：克隆标识 + 复制指纹。
"""
from __future__ import annotations

import hashlib

from ..config import SceneCfg
from ..core.rng import rng_for
from .common import is_equipped


def _unit(vid: str) -> float:
    h = hashlib.sha256(vid.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def baseline_fingerprint(vid: str) -> dict[str, float]:
    u = _unit(vid)
    u2 = _unit(vid + ":2")
    u3 = _unit(vid + ":3")
    return {
        "cfo_ppm": round(-3.0 + 6.0 * u, 3),
        "iq_gain_db": round(-0.8 + 1.6 * u2, 3),
        "iq_phase_deg": round(-4.0 + 8.0 * u3, 2),
        "rssi_base_dbm": round(-58.0 - 12.0 * u, 1),
    }


class AuthLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.rng = rng_for(scene.seed, "auth:events")
        self._issued: set[str] = set()
        self._rotate_at: dict[str, float] = {}
        self._pseudo_idx: dict[str, int] = {}

    def tick(self, ctx) -> None:
        t = ctx.t
        for v in ctx.vehicles.values():
            if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                continue
            if v.vid not in self._issued:
                self._issued.add(v.vid)
                self._pseudo_idx[v.vid] = 1
                self._rotate_at[v.vid] = t + float(self.rng.uniform(6.0, 12.0))
                ctx.emit("auth.cert", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "event": "issue",
                    "cert_id": f"CERT-{v.vid}-1",
                    "pseudonym": f"PN-{v.vid}-1",
                    "status": "valid",
                })
            elif t >= self._rotate_at.get(v.vid, 1e9):
                self._pseudo_idx[v.vid] += 1
                idx = self._pseudo_idx[v.vid]
                self._rotate_at[v.vid] = t + float(self.rng.uniform(6.0, 12.0))
                ctx.emit("auth.cert", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "event": "rotate",
                    "cert_id": f"CERT-{v.vid}-{idx}",
                    "pseudonym": f"PN-{v.vid}-{idx}",
                    "status": "valid",
                })
        # 指纹 1 Hz
        if int(t) != int(t - ctx.step_s):
            for v in ctx.vehicles.values():
                if not is_equipped(v.vid, self.scene.vehicles.equipped_ratio):
                    continue
                base = baseline_fingerprint(v.vid)
                ctx.emit("auth.fingerprint", v.vid, f"veh:{v.vid}", {
                    "node_id": v.vid,
                    "cfo_ppm": round(base["cfo_ppm"] + float(self.rng.normal(0.0, 0.03)), 3),
                    "iq_gain_db": round(base["iq_gain_db"] + float(self.rng.normal(0.0, 0.02)), 3),
                    "iq_phase_deg": round(base["iq_phase_deg"] + float(self.rng.normal(0.0, 0.08)), 2),
                    "rssi_dbm": round(base["rssi_base_dbm"] + float(self.rng.normal(0.0, 1.2)), 1),
                    "auth_result": "pass",
                })
