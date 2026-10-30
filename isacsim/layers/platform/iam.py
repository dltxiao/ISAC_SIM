"""IAM：token 签发/校验/吊销。"""
from __future__ import annotations

from .model import PlatformToken


class IAM:
    def __init__(self) -> None:
        self.tokens: dict[str, PlatformToken] = {}
        self._seq = 0
        self.issued = 0
        self.rejected = 0

    def issue(self, ctx, *, subject: str, tenant: str, scopes: list[str],
              ttl_s: float, span=None) -> PlatformToken:
        self._seq += 1
        token = PlatformToken(
            token_id=f"tok-{self._seq:04d}",
            subject=subject,
            tenant=tenant,
            scopes=list(scopes),
            issued_at=ctx.t,
            expires_at=ctx.t + ttl_s,
        )
        self.tokens[token.token_id] = token
        self.issued += 1
        ctx.emit("app.auth", subject, "platform:V2X-01", {
            "msg_type": "token_issue",
            "token_id": token.token_id,
            "subject": subject,
            "tenant": tenant,
            "scopes": list(scopes),
            "result": "accept",
            "reason": None,
            "latency_ms": 1.0,
        }, span=span)
        return token

    def validate(self, ctx, token_id: str, scope: str, *, span=None,
                 emit: bool = True) -> tuple[bool, str]:
        token = self.tokens.get(token_id)
        if token is None:
            ok, reason = False, "unknown_token"
        elif token.revoked:
            ok, reason = False, "revoked"
        elif ctx.t > token.expires_at:
            ok, reason = False, "expired"
        elif scope not in token.scopes and "admin" not in token.scopes:
            ok, reason = False, "scope_denied"
        else:
            ok, reason = True, "ok"
        if not ok:
            self.rejected += 1
        if emit:
            ctx.emit("app.auth", token.subject if token else "unknown",
                     "platform:V2X-01", {
                         "msg_type": "token_validate",
                         "token_id": token_id,
                         "tenant": token.tenant if token else None,
                         "scopes": list(token.scopes) if token else [],
                         "required_scope": scope,
                         "result": "accept" if ok else "reject",
                         "reason": reason,
                         "latency_ms": 1.0,
                     }, span=span)
        return ok, reason

    def revoke(self, ctx, token_id: str, *, span=None) -> bool:
        token = self.tokens.get(token_id)
        if token is None:
            return False
        token.revoked = True
        ctx.emit("app.auth", token.subject, "platform:V2X-01", {
            "msg_type": "token_revoke",
            "token_id": token_id,
            "tenant": token.tenant,
            "scopes": list(token.scopes),
            "result": "accept",
            "reason": "revoked",
            "latency_ms": 1.0,
        }, span=span)
        return True

    def snapshot(self) -> dict:
        tokens = sorted(self.tokens.values(), key=lambda t: t.token_id)
        rows = [{
            "token_id": token.token_id,
            "subject": token.subject,
            "tenant": token.tenant,
            "scopes": ",".join(token.scopes),
            "issued_at": round(token.issued_at, 3),
            "expires_at": round(token.expires_at, 3),
            "revoked": token.revoked,
        } for token in tokens[:40]]
        return {
            "tokens": len(self.tokens),
            "active_tokens": sum(1 for t in self.tokens.values()
                                 if not t.revoked and t.expires_at > 0),
            "issued": self.issued,
            "rejected": self.rejected,
            # 有界只读列表，供 trace/Web 使用；tokens 计数语义不变。
            "token_list": rows,
            "token_list_total": len(tokens),
            "token_list_truncated": len(tokens) > 40,
        }
