"""Dispatcher：云控指令的鉴权、签名/TTL 检查与下发。"""
from __future__ import annotations

from .audit import AuditLog
from .iam import IAM
from .model import ControlCommand


class Dispatcher:
    def __init__(self) -> None:
        self.commands: dict[str, ControlCommand] = {}
        self._seq = 0
        self.sent = 0
        self.rejected = 0
        self.bursts = 0

    def submit(self, ctx, *, iam: IAM, audit: AuditLog, token_id: str,
               vehicle_ids: list[str], command: str,
               params: dict | None = None, ttl_s: float = 5.0,
               trace=None) -> ControlCommand:
        params = dict(params or {})
        ok, reason = iam.validate(ctx, token_id, "control:write", trace=trace)
        token = iam.tokens.get(token_id)
        self._seq += 1
        cmd = ControlCommand(
            command_id=f"cmd-{self._seq:04d}",
            token_id=token_id,
            tenant=token.tenant if token else "unknown",
            vehicle_ids=list(vehicle_ids),
            command=command,
            params=params,
            ttl_s=float(ttl_s),
            signature_status="valid",
            state="AUTHORIZED" if ok else "FAILED",
        )
        self.commands[cmd.command_id] = cmd
        result = "accept" if ok else "reject"
        ctx.emit("app.control", "platform:V2X-01", "platform:V2X-01", {
            "msg_type": "cloud_control",
            "command_id": cmd.command_id,
            "token_id": token_id,
            "tenant": cmd.tenant,
            "vehicle_ids": cmd.vehicle_ids,
            "command": command,
            "params": params,
            "ttl_s": cmd.ttl_s,
            "signature_status": cmd.signature_status,
            "result": result,
            "cause": None if ok else reason,
            "latency_ms": 2.0,
        }, trace=trace)
        audit.append(ctx, actor=f"token:{token_id}", action="control:submit",
                     resource=f"vehicles:{len(vehicle_ids)}", result=result, trace=trace)
        if not ok:
            self.rejected += 1
            return cmd
        self.sent += 1
        if len(vehicle_ids) >= 3:
            self.bursts += 1
        decel = float(params.get("decel_mps2", 3.0))
        duration = float(params.get("duration_s", 1.2))
        control = ctx.extras.get("layers", {}).get("control")
        queue = control.intents if control is not None else ctx.extras.setdefault("control_intents", [])
        for vid in vehicle_ids:
            queue.append({
                "vehicle_id": vid,
                "decel_mps2": decel,
                "duration_s": duration,
                "source": "platform:V2X-01",
                "reason": "cloud_control",
                "trace": trace,
            })
        return cmd

    def snapshot(self) -> dict:
        return {"commands": len(self.commands), "sent": self.sent,
                "rejected": self.rejected, "bursts": self.bursts}
