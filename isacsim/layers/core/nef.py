"""NEF：API 订阅/token/事件通知的语义近似。

M2 只实现与 A8 相关的核心行为：签发/校验 token、订阅/退订、通知。
"""
from __future__ import annotations

from ...config import SceneCfg
from .model import NefSubscription, NefToken
from .nf import NetworkFunction


class NEF(NetworkFunction):
    def __init__(self, scene: SceneCfg, factory) -> None:
        super().__init__("nef:NEF-01", capacity=scene.core.nef_capacity_qps,
                         queue_capacity=scene.core.queue_capacity,
                         base_latency_ms=5.0, factory=factory)
        self.scene = scene
        self.tokens: dict[str, NefToken] = {}
        self.subscriptions: dict[str, NefSubscription] = {}
        self._token_seq = 0
        self._sub_seq = 0
        self.unsubscribed = 0
        self.revoked = 0

    # ------------------------------------------------------------------
    def issue_token(self, ctx, *, subject: str, tenant: str,
                    scopes: list[str], ttl_s: float = 3600.0,
                    trace=None) -> NefToken:
        self._token_seq += 1
        token = NefToken(
            token_id=f"nef-tok-{self._token_seq:04d}",
            subject=subject,
            tenant=tenant,
            scopes=list(scopes),
            issued_at=ctx.t,
            expires_at=ctx.t + ttl_s,
        )
        self.tokens[token.token_id] = token
        ctx.emit("core.nef", self.entity, self.entity, {
            "msg_type": "token_issue",
            "token_id": token.token_id,
            "subject": subject,
            "tenant": tenant,
            "scopes": list(scopes),
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, trace=trace)
        return token

    def validate(self, token_id: str, scope: str, now: float) -> tuple[bool, str]:
        token = self.tokens.get(token_id)
        if token is None:
            return False, "unknown_token"
        if token.revoked:
            return False, "revoked"
        if now > token.expires_at:
            return False, "expired"
        if scope not in token.scopes and "admin" not in token.scopes:
            return False, "scope_denied"
        return True, "ok"

    def subscribe(self, ctx, *, token_id: str, event_types: list[str],
                  callback: str, trace=None) -> NefSubscription | None:
        ok, reason = self.validate(token_id, "nef:subscribe", ctx.t)
        if not ok:
            self.rejected += 1
            ctx.emit("core.nef", self.entity, self.entity, {
                "msg_type": "api_subscribe",
                "token_id": token_id,
                "event_types": event_types,
                "result": "reject",
                "cause": reason,
                "latency_ms": self.base_latency_ms,
            }, trace=trace)
            return None
        token = self.tokens[token_id]
        self._sub_seq += 1
        sub = NefSubscription(
            sub_id=f"nef-sub-{self._sub_seq:04d}",
            af_id=token.subject,
            tenant=token.tenant,
            event_types=list(event_types),
            callback=callback,
            scopes=list(token.scopes),
        )
        self.subscriptions[sub.sub_id] = sub
        ctx.emit("core.nef", self.entity, self.entity, {
            "msg_type": "api_subscribe",
            "subscription_id": sub.sub_id,
            "token_id": token_id,
            "tenant": sub.tenant,
            "event_types": list(event_types),
            "callback": callback,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, trace=trace)
        return sub

    def unsubscribe(self, ctx, *, token_id: str, sub_id: str, trace=None) -> bool:
        ok, reason = self.validate(token_id, "nef:subscribe", ctx.t)
        sub = self.subscriptions.get(sub_id)
        if not ok or sub is None:
            self.rejected += 1
            ctx.emit("core.nef", self.entity, self.entity, {
                "msg_type": "api_unsubscribe",
                "subscription_id": sub_id,
                "token_id": token_id,
                "result": "reject",
                "cause": reason if not ok else "unknown_subscription",
                "latency_ms": self.base_latency_ms,
            }, trace=trace)
            return False
        sub.state = "REVOKED"
        self.unsubscribed += 1
        ctx.emit("core.nef", self.entity, self.entity, {
            "msg_type": "api_unsubscribe",
            "subscription_id": sub_id,
            "token_id": token_id,
            "tenant": sub.tenant,
            "event_types": sub.event_types,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, trace=trace)
        return True

    def revoke_token(self, ctx, token_id: str, trace=None) -> bool:
        token = self.tokens.get(token_id)
        if token is None:
            return False
        token.revoked = True
        self.revoked += 1
        ctx.emit("core.nef", self.entity, self.entity, {
            "msg_type": "token_revoke",
            "token_id": token_id,
            "tenant": token.tenant,
            "result": "accept",
            "latency_ms": self.base_latency_ms,
        }, trace=trace)
        return True

    def snapshot(self) -> dict:
        out = super().snapshot()
        out.update({
            "tokens": len(self.tokens),
            "active_tokens": sum(1 for t in self.tokens.values()
                                 if not t.revoked and t.expires_at > 0),
            "subscriptions": len(self.subscriptions),
            "active_subscriptions": sum(1 for s in self.subscriptions.values()
                                        if s.state == "ACTIVE"),
            "unsubscribed": self.unsubscribed,
            "revoked": self.revoked,
        })
        return out
