"""攻击调度器：接收 Web/CLI 下发的请求，按仿真时钟启动、推进、结束。"""
from __future__ import annotations

from typing import Any

from ..config import SceneCfg
from .base import AttackInstance, AttackSpec
from .campaigns import CAMPAIGNS
from .primitives import PrimitiveAttack

MAX_ACTIVE = 40
READY_TIMEOUT_S = 1.0      # 前置条件最多等这么久；超时记为 aborted 并写真相


class AttackScheduler:
    def __init__(self, scene: SceneCfg, stats, truth, seed: int, run_id: str) -> None:
        self.scene = scene
        self.stats = stats
        self.truth = truth
        self.seed = seed
        self.run_id = run_id
        self.instances: list[AttackInstance] = []
        self._counter = 0

    # ------------------------------------------------------------------
    def submit(self, spec: AttackSpec) -> list[AttackInstance]:
        count = max(1, min(20, int(spec.count)))
        active = sum(1 for i in self.instances if not i.finished)
        if active + count > MAX_ACTIVE:
            raise ValueError(f"并发攻击超过上限 {MAX_ACTIVE}（当前 {active}，本次 {count}）")
        created: list[AttackInstance] = []
        base_start = spec.start
        for i in range(count):
            self._counter += 1
            aid = f"{self.run_id}-ATK{self._counter:03d}"
            sub = AttackSpec(
                type=spec.type, count=1, target=spec.target,
                amplitude=spec.amplitude, duration=spec.duration,
                start=spec.start, params=dict(spec.params),
            )
            cls = CAMPAIGNS.get(spec.type)
            inst = (cls(sub, self.scene, self.seed + i * 7919, aid) if cls
                    else PrimitiveAttack(sub, self.scene, self.seed + i * 7919, aid))
            inst.offset = i * 2.0
            inst.start_at = -1.0
            inst.queued = True
            self.instances.append(inst)
            created.append(inst)
        return created

    # ------------------------------------------------------------------
    def tick(self, ctx) -> None:
        for inst in list(self.instances):
            if inst.finished:
                continue
            if inst.cancel_requested:
                # 取消请求必须带回上下文收尾：摘 hook、恢复全局状态、记实际结束时刻
                inst.stop(ctx)
                if inst.started:
                    self.stats.campaign_finished()
                self.stats.note("attack", f"{inst.atype} 取消（{inst.id}）")
                continue
            if not inst.started:
                if inst.submitted_t is None:
                    inst.submitted_t = ctx.t
                    base = inst.spec.start if inst.spec.start is not None else ctx.t
                    inst.start_at = base + inst.offset
                if ctx.t + 1e-9 >= inst.start_at:
                    ok, why = inst.ready(ctx)
                    if not ok:
                        inst.blocked_reason = why
                        waited = ctx.t - max(inst.start_at, inst.submitted_t)
                        if waited < READY_TIMEOUT_S:
                            continue          # 保持排队，等前置条件
                        inst.abort(ctx, why)
                    # 证据基线必须在 start() 之前取：start 内发出的记录也算“攻击期间”
                    inst._stream_base = dict(getattr(ctx.stats, "stream_records", {}))
                    if not getattr(inst, "aborted", ""):
                        inst.start(ctx)
                    inst.queued = False
                    if inst.finished and not getattr(inst, "aborted", ""):
                        # 剧本自判“当前不可执行”：保持旧行为，不计数、不写真相
                        continue
                    if inst.is_campaign:
                        self.stats.campaign_started(inst.atype)
                    else:
                        self.stats.primitive_injected(inst.kind)
                    ev = inst.truth()
                    inst.truth_event = ev
                    self.truth.add(ev)
                    if inst.finished:
                        self.stats.campaign_finished()
                        self.stats.note("attack", f"{inst.atype} 前置条件不满足（{why}）")
                    continue
            else:
                if ctx.t >= inst.t1 - 1e-9:
                    inst.stop(ctx)
                    self.stats.campaign_finished()
                    self.stats.note("attack", f"{inst.atype} 结束（{inst.id}）")
                else:
                    inst.tick(ctx)

    # ------------------------------------------------------------------
    def cancel(self, attack_id: str) -> bool:
        """只登记取消意图；真正的 stop(ctx) 在下一 tick 执行（需要上下文）。"""
        for inst in self.instances:
            if inst.id == attack_id and not inst.finished and not inst.cancel_requested:
                inst.cancel_requested = True
                return True
        return False

    def active(self) -> list[AttackInstance]:
        return [i for i in self.instances if i.started and not i.finished]

    def snapshot(self) -> list[dict[str, Any]]:
        return [i.snapshot() for i in self.instances[-60:]]
