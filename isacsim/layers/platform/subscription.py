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

    def shrink(self, ctx, sub_id: str, event_types: list[str], *, span=None) -> list[str]:
        """从订阅里移除若干事件类型；全部移除时置 REVOKED。

        返回真正被移除的类型。用于“退订范围”随强度变化的剧本（A8）：
        平台的一次退订可以只丢掉 denm，也可以把 cpm/spat 一起丢掉。
        """
        sub = self.subs.get(sub_id)
        if sub is None:
            return []
        removed = [e for e in event_types if e in sub.event_types]
        if not removed:
            return []
        for e in removed:
            sub.event_types.remove(e)
        if not sub.event_types:
            sub.state = "REVOKED"
            self.revoked += 1
        ctx.emit("app.subscription", f"tenant:{sub.tenant}", "platform:V2X-01", {
            "msg_type": "unsubscribe",
            "tenant": sub.tenant,
            "subscription_id": sub.sub_id,
            "event_types": removed,
            "remaining": list(sub.event_types),
            "callback": sub.callback,
            "action": "revoke",
            "result": "accept",
            "latency_ms": 2.0,
        }, span=span)
        return removed

    def add_events(self, ctx, sub_id: str, event_types: list[str], *, span=None) -> list[str]:
        """把若干事件类型加回订阅（合法运维恢复）。与 shrink 对称。"""
        sub = self.subs.get(sub_id)
        if sub is None:
            return []
        added = [e for e in event_types if e not in sub.event_types]
        if not added:
            return []
        sub.event_types.extend(added)
        if sub.state == "REVOKED" and sub.event_types:
            sub.state = "ACTIVE"
        ctx.emit("app.subscription", f"tenant:{sub.tenant}", "platform:V2X-01", {
            "msg_type": "subscribe",
            "tenant": sub.tenant,
            "subscription_id": sub.sub_id,
            "event_types": added,
            "remaining": list(sub.event_types),
            "callback": sub.callback,
            "action": "update",
            "result": "accept",
            "latency_ms": 2.0,
        }, span=span)
        return added

    def active_for(self, event_type: str) -> list[Subscription]:
        return [s for s in self.subs.values()
                if s.state == "ACTIVE" and event_type in s.event_types]

    def snapshot(self) -> dict:
        subs = sorted(self.subs.values(), key=lambda s: s.sub_id)
        rows = [{
            "sub_id": sub.sub_id,
            "tenant": sub.tenant,
            "event_types": ",".join(sub.event_types),
            "callback": sub.callback,
            "state": sub.state,
        } for sub in subs[:40]]
        return {
            "subscriptions": len(self.subs),
            "active": sum(1 for s in self.subs.values() if s.state == "ACTIVE"),
            "revoked": self.revoked,
            # 有界只读列表，供 trace/Web 使用；计数语义不变。
            "subscription_list": rows,
            "subscription_list_total": len(subs),
            "subscription_list_truncated": len(subs) > 40,
        }
