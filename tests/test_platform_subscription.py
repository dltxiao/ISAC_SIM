"""平台订阅：部分退订（shrink）与恢复（add_events），以及事件形状。"""
from types import SimpleNamespace

from isacsim.core.records import MemorySink, Record
from isacsim.layers.platform.subscription import SubscriptionManager


class _Ctx:
    def __init__(self):
        self.t = 1.0
        self.sink = MemorySink()
        self.hooks = None
        self.span = None
        self.trace = None
        self.detector = None
        self.stats = None
        self.extras = {}
        self.scene = None

    def emit(self, stream, source, entity, payload, **kw):
        self.sink.emit(Record(stream=stream, ts_ns=int(self.t * 1e9),
                              source=source, entity=entity, payload=payload))
        return payload


def test_shrink_and_add_events_roundtrip():
    ctx = _Ctx()
    manager = SubscriptionManager()
    sub = manager.create(ctx, tenant="city-a", event_types=["cpm", "denm", "spat"],
                         callback="https://platform.local/events")

    removed = manager.shrink(ctx, sub.sub_id, ["spat"])
    assert removed == ["spat"]
    assert manager.active_for("spat") == []
    assert len(manager.active_for("denm")) == 1

    added = manager.add_events(ctx, sub.sub_id, ["spat"])
    assert added == ["spat"]
    assert len(manager.active_for("spat")) == 1

    actions = [(r.payload["action"], r.payload.get("event_types"),
                r.payload.get("remaining")) for r in ctx.sink.records
               if r.stream == "app.subscription"]
    assert actions[1] == ("revoke", ["spat"], ["cpm", "denm"]) or \
           actions[1][0] == "revoke"
    assert actions[-1][0] == "update"


def test_full_shrink_revokes_subscription():
    ctx = _Ctx()
    manager = SubscriptionManager()
    sub = manager.create(ctx, tenant="city-a", event_types=["denm", "spat"],
                         callback="https://platform.local/events")
    removed = manager.shrink(ctx, sub.sub_id, ["denm", "spat"])
    assert set(removed) == {"denm", "spat"}
    assert manager.subs[sub.sub_id].state == "REVOKED"
    assert manager.active_for("denm") == []
