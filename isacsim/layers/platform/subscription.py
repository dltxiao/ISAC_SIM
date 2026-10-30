"""平台订阅：租户对 CPM/DENM/SPaT/control 的订阅与撤销。"""
from __future__ import annotations

from .model import Subscription


class SubscriptionManager:
    def __init__(self) -> None:
        self.subs: dict[str, Subscription] = {}
        self._seq = 0
        self.revoked = 0

    def create(self, ctx, *, tenant: str, event_types: list[str],
               callback: str, span=None) -> Subscription:
        self._seq += 1
        sub = Subscription(
            sub_id=f"sub-{self._seq:04d}",
            tenant=tenant,
            event_types=list(event_types),
            callback=callback,
        )
        self.subs[sub.sub_id] = sub
        ctx.emit("app.subscription", f"tenant:{tenant}", "platform:V2X-01", {
            "msg_type": "subscribe",
            "tenant": tenant,
            "subscription_id": sub.sub_id,
            "event_types": list(event_types),
            "callback": callback,
            "action": "create",
            "result": "accept",
            "latency_ms": 2.0,
        }, span=span)
        return sub

    def revoke(self, ctx, sub_id: str, *, span=None) -> bool:
        sub = self.subs.get(sub_id)
        if sub is None:
            return False
        sub.state = "REVOKED"
        self.revoked += 1
        ctx.emit("app.subscription", f"tenant:{sub.tenant}", "platform:V2X-01", {
            "msg_type": "unsubscribe",
            "tenant": sub.tenant,
            "subscription_id": sub.sub_id,
            "event_types": list(sub.event_types),
            "callback": sub.callback,
            "action": "revoke",
            "result": "accept",
            "latency_ms": 2.0,
        }, span=span)
        return True

    def active_for(self, event_type: str) -> list[Subscription]:
        return [s for s in self.subs.values()
                if s.state == "ACTIVE" and event_type in s.event_types]

    def snapshot(self) -> dict:
        return {
            "subscriptions": len(self.subs),
            "active": sum(1 for s in self.subs.values() if s.state == "ACTIVE"),
            "revoked": self.revoked,
        }
