"""攻击调度器：接收 Web/CLI 下发的请求，按仿真时钟启动、推进、结束。"""
from __future__ import annotations

from typing import Any

from ..config import SceneCfg
from .base import AttackInstance, AttackSpec
from .campaigns import CAMPAIGNS
from .primitives import PrimitiveAttack

MAX_ACTIVE = 40


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
            if not inst.started:
                if inst.submitted_t is None:
                    inst.submitted_t = ctx.t
                    base = inst.spec.start if inst.spec.start is not None else ctx.t
                    inst.start_at = base + inst.offset
                if ctx.t + 1e-9 >= inst.start_at:
                    inst.start(ctx)
                    inst.queued = False
                    if inst.finished:
                        continue
                    if inst.is_campaign:
                        self.stats.campaign_started(inst.atype)
                    else:
                        self.stats.primitive_injected(inst.kind)
                    self.truth.add(inst.truth())
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
        for inst in self.instances:
            if inst.id == attack_id and not inst.finished:
                inst.finished = True
                return True
        return False

    def active(self) -> list[AttackInstance]:
        return [i for i in self.instances if i.started and not i.finished]

    def snapshot(self) -> list[dict[str, Any]]:
        return [i.snapshot() for i in self.instances[-60:]]
