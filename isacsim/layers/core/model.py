"""核心网语义模型：UE 上下文、PDU 会话、QoS flow、UPF 流表。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UEContext:
    ue_id: str
    supi: str
    gnb_id: str
    state: str = "REGISTERED"
    amf_ue_ngap_id: int = 0
    snssai: str = "01-000001"
    pdu_session_id: str | None = None
    session_active: bool = False


@dataclass
class QoSProfile:
    qfi: int
    five_qi: int = 79
    gfbr_kbps: int = 1000
    mfbr_kbps: int = 2000
    preemption: str = "may"


@dataclass
class PDUSession:
    session_id: str
    ue_id: str
    dnn: str
    snssai: str
    upf_id: str
    ue_ip: str
    qos_flows: dict[int, QoSProfile] = field(default_factory=dict)
    state: str = "ACTIVE"


@dataclass
class FlowRule:
    flow_id: str
    ue_id: str
    session_id: str
    qfi: int
    five_qi: int
    teid: int
    route: str = "dn:DN-01"
    state: str = "INSTALLED"


@dataclass
class Subscriber:
    supi: str
    dnn: str
    snssai: str
    barred: bool = False


@dataclass
class AuthResult:
    ok: bool
    vector_id: str
    latency_ms: float
    cause: str | None = None
