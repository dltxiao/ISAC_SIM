"""审计日志：append-only，序列 + 前序哈希。"""
from __future__ import annotations

import hashlib

from .model import AuditRecord


class AuditLog:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.seq = 0
        self.prev_hash = "genesis"
        self.records: list[AuditRecord] = []
        self.suppressed = 0

    def append(self, ctx, *, actor: str, action: str, resource: str,
               result: str, trace=None) -> AuditRecord | None:
        if not self.enabled:
            self.suppressed += 1
            return None
        self.seq += 1
        raw = f"{self.prev_hash}|{self.seq}|{actor}|{action}|{resource}|{result}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        rec = AuditRecord(
            audit_seq=self.seq,
            actor=actor,
            action=action,
            resource=resource,
            result=result,
            prev_hash=self.prev_hash,
            hash=digest,
        )
        self.records.append(rec)
        ctx.emit("app.audit", actor, "platform:V2X-01", {
            "actor": actor,
            "action": action,
            "resource": resource,
            "result": result,
            "audit_seq": rec.audit_seq,
            "prev_hash": rec.prev_hash,
            "hash": rec.hash,
        }, trace=trace)
        self.prev_hash = digest
        return rec

    def suppress(self, n: int = 1) -> None:
        """模拟审计被抑制：只推进序列，不写记录，制造 audit_seq 缺口。"""
        self.seq += max(0, int(n))
        self.suppressed += max(0, int(n))

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "audit_seq": self.seq,
            "records": len(self.records),
            "suppressed": self.suppressed,
        }
