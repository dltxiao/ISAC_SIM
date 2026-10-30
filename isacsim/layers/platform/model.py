"""V2X 平台语义模型：token、订阅、云控、审计。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlatformToken:
    token_id: str
    subject: str
    tenant: str
    scopes: list[str]
    issued_at: float
    expires_at: float
    revoked: bool = False


@dataclass
class Subscription:
    sub_id: str
    tenant: str
    event_types: list[str]
    callback: str
    state: str = "ACTIVE"


@dataclass
class ControlCommand:
    command_id: str
    token_id: str
    tenant: str
    vehicle_ids: list[str]
    command: str
    params: dict = field(default_factory=dict)
    ttl_s: float = 5.0
    signature_status: str = "valid"
    state: str = "RECEIVED"


@dataclass
class AuditRecord:
    audit_seq: int
    actor: str
    action: str
    resource: str
    result: str
    prev_hash: str
    hash: str
