"""告警级攻击链构建器。

M4 先做“可解释的链骨架”：按时间聚类告警，提取实体节点、根因节点和阶段；
不读取 truth，也不做深度图推理。后续可由外部模型替换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CORE_PREFIXES = ("amf:", "smf:", "upf:", "nef:", "udm:", "ausf:", "pcf:", "nrf:")
APP_PREFIXES = ("platform:", "tenant:", "token:", "session:", "flow:")
RAN_PREFIXES = ("gnb:", "rsu:", "net:")


def layer_of(entity: str) -> str:
    if entity.startswith(CORE_PREFIXES):
        return "core"
    if entity.startswith(APP_PREFIXES):
        return "app"
    if entity.startswith(RAN_PREFIXES):
        return "ran"
    return "edge"


@dataclass
class ChainNode:
    id: str
    layer: str
    role: str = "observed"


@dataclass
class ChainEdge:
    src: str
    dst: str
    relation: str
    t: float


@dataclass
class Chain:
    chain_id: str
    t0: float
    t1: float
    nodes: list[ChainNode] = field(default_factory=list)
    edges: list[ChainEdge] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    root_cause: str | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "t0": round(self.t0, 3),
            "t1": round(self.t1, 3),
            "nodes": [{"id": n.id, "layer": n.layer, "role": n.role} for n in self.nodes],
            "edges": [{"src": e.src, "dst": e.dst, "relation": e.relation,
                       "t": round(e.t, 3)} for e in self.edges],
            "stages": self.stages,
            "root_cause": self.root_cause,
            "confidence": round(self.confidence, 3),
        }


def build_chains(alerts: list[Any], gap: float = 2.5) -> list[Chain]:
    if not alerts:
        return []
    rows = [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in alerts]
    rows.sort(key=lambda a: a.get("t", 0.0))
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for row in rows:
        if cur and float(row["t"]) - float(cur[-1]["t"]) > gap:
            groups.append(cur)
            cur = []
        cur.append(row)
    if cur:
        groups.append(cur)

    chains: list[Chain] = []
    for i, group in enumerate(groups, 1):
        nodes: dict[str, ChainNode] = {}
        stages: list[dict[str, Any]] = []
        for row in group:
            entity = str(row.get("entity") or "unknown")
            if entity not in nodes:
                nodes[entity] = ChainNode(id=entity, layer=layer_of(entity))
            stages.append({
                "name": row.get("rule", "alert"),
                "t0": round(float(row.get("t", 0.0)), 3),
                "t1": round(float(row.get("t", 0.0)), 3),
                "layer": layer_of(entity),
                "entity": entity,
            })
        root = group[0].get("entity") if group else None
        if root in nodes:
            nodes[root].role = "root"
        edges: list[ChainEdge] = []
        for row in group[1:]:
            dst = str(row.get("entity") or "unknown")
            if root and dst != root:
                edges.append(ChainEdge(root, dst, "causes", float(row.get("t", 0.0))))
        confidence = min(0.99, 0.45 + 0.08 * len(nodes) + 0.02 * len(group))
        chains.append(Chain(
            chain_id=f"chain-{i:04d}",
            t0=float(group[0].get("t", 0.0)),
            t1=float(group[-1].get("t", 0.0)),
            nodes=list(nodes.values()),
            edges=edges,
            stages=stages,
            root_cause=root,
            confidence=confidence,
        ))
    return chains


class ChainBuilder:
    """按需从告警构建链；保留类接口，后续可改为流式。"""

    def build(self, alerts: list[Any], gap: float = 2.5) -> list[Chain]:
        return build_chains(alerts, gap=gap)
