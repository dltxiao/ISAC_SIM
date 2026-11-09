"""NF 排队模型：准入/卸载/排空/时延（docs/09 §4.4 的公式）。"""
from types import SimpleNamespace

from isacsim.layers.core.nf import NetworkFunction


class _NF(NetworkFunction):
    def __init__(self, capacity=10.0, queue=100, shed_at=0.5):
        super().__init__("nf:TEST", capacity=capacity, queue_capacity=queue,
                         base_latency_ms=2.0, shed_at=shed_at)


def _ctx(t):
    return SimpleNamespace(t=t, extras={})


def test_accept_sheds_at_watermark_and_drains_on_tick():
    nf = _NF(capacity=10.0, queue=20, shed_at=0.5)
    ctx = _ctx(0.0)
    nf.tick(ctx)
    # 前 10 条（水位 50%）接纳，之后开始丢
    accepted = sum(1 for _ in range(20) if nf.accept(ctx))
    assert accepted == 10
    assert nf.received == 20
    assert nf.dropped == 10
    assert nf.queue_depth == 10
    # 过 1 s：服务能力 10 条，队列排空
    nf.tick(_ctx(1.0))
    assert nf.queue_depth == 0
    assert nf.overloaded is False


def test_queue_raises_latency_and_overload_flag():
    nf = _NF(capacity=10.0, queue=20, shed_at=0.9)
    ctx = _ctx(0.0)
    nf.tick(ctx)
    base = nf.latency_ms
    for _ in range(15):
        nf.accept(ctx)
    assert nf.queue_depth == 15
    assert nf.latency_ms > base
    # 队列 15/20 = 75% < 0.9，但 tick 里按水位重算
    nf.tick(_ctx(0.0))
    assert nf.overloaded is False
    nf.tick(_ctx(0.1))          # 只排空 1 条
    assert nf.queue_depth == 14
    assert nf.snapshot()["queue_depth"] == 14


def test_hold_only_affects_display_not_admission():
    nf = _NF(capacity=10.0, queue=20, shed_at=0.3)
    ctx = _ctx(0.0)
    nf.tick(ctx)
    nf.set_hold(10)
    nf.tick(ctx)
    assert nf.load_hint == 0.5          # 显示负载被抬到 50%
    assert nf.queue_depth == 0          # 真实队列仍空
    assert nf.accept(ctx) is True       # 准入不受 hold 影响
