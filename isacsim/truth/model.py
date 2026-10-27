"""真值模型与落盘。

真值只由生成器产出，检测侧不可读；评估侧按目录读取。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import GENERATOR_VERSION


OFFICIAL_TYPES = {
    "A1": "感知数据篡改诱导通信拥塞",
    "A2": "物理环境欺骗+控制指令误导",
    "A3": "跨层隐蔽攻击",
    "A4": "双因素仿冒攻击",
    "A5": "5G-A 信令风暴",
    "A7": "UPF/N6 流量劫持",
    "A9": "平台越权/云控注入",
}

OFFICIAL_CHAIN = {
    "A1": ["sensing.objects:inject", "adas:ttc_brake", "denm:flood", "pc5:cbr_up"],
    "A2": ["sensing.objects:spoof_sign", "control.command:inject", "link:jam", "veh:hard_brake"],
    "A3": ["radar.echo:range_ramp", "radar.track:bias", "control.command:inject", "telemetry:mask"],
    # A4 当前只作用于认证与 V2X；uu:ghost_attach 尚未实现，不能写进真值。
    "A4": ["auth.cert:clone", "auth.fingerprint:replay", "v2x.cam:spoof"],
    "A5": ["core.nas:registration_flood", "core.session:establish_fail",
           "app.ingest:age_up", "control.command:delay"],
    "A7": ["core.upf:route_change", "v2x.cpm:forge", "app.ingest:provenance_mismatch",
           "control.command:inject"],
    "A9": ["app.auth:token_reuse", "app.control:burst", "control.command:batch_brake",
           "v2x.denm:emergency"],
}


@dataclass
class TruthEvent:
    event_id: str
    campaign: str
    t0: float
    t1: float
    nodes: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    amplitude: float = 0.5
    envelope: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    chain: dict[str, Any] | None = None
    label: int = 1
    seed: int = 0
    generator_version: str = GENERATOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        out = {
            "event_id": self.event_id,
            "campaign": self.campaign,
            "official_type": OFFICIAL_TYPES.get(self.campaign, self.campaign),
            "label": self.label,
            "t0": round(self.t0, 3),
            "t1": round(self.t1, 3),
            "nodes": self.nodes,
            "domains": self.domains,
            "params": self.params,
            "amplitude": round(self.amplitude, 3),
            "envelope": self.envelope,
            "stages": self.stages,
            "causal_chain": self.causal_chain or OFFICIAL_CHAIN.get(self.campaign, []),
            "expected_evidence": self.expected_evidence,
            "seed": self.seed,
            "generator_version": self.generator_version,
        }
        if self.chain is not None:
            out["chain"] = self.chain
        return out


class TruthStore:
    def __init__(self, out_dir: str | Path | None = None) -> None:
        self.dir = Path(out_dir) if out_dir else None
        self.events: list[TruthEvent] = []
        self._fh = None
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = (self.dir / "events.jsonl").open("w", encoding="utf-8")

    def add(self, event: TruthEvent) -> None:
        self.events.append(event)
        if self._fh is not None:
            self._fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def write_manifest(self, extra: dict[str, Any] | None = None) -> None:
        if self.dir is None:
            return
        payload = {"events": len(self.events), "generator_version": GENERATOR_VERSION}
        if extra:
            payload.update(extra)
        (self.dir / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
