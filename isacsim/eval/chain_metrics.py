"""链级评估：节点召回/精确率、根因 top-1、阶段覆盖。"""
from __future__ import annotations

from statistics import mean
from typing import Any


def _node_ids(items: list[Any]) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict) and item.get("id"):
            out.add(str(item["id"]))
    return out


def truth_nodes(event: dict[str, Any]) -> set[str]:
    chain = event.get("chain") or {}
    nodes = _node_ids(chain.get("nodes") or [])
    return nodes or _node_ids(event.get("nodes") or [])


def pred_nodes(chain: dict[str, Any]) -> set[str]:
    return _node_ids(chain.get("nodes") or [])


def truth_root(event: dict[str, Any]) -> str | None:
    chain = event.get("chain") or {}
    if chain.get("root_cause"):
        return str(chain["root_cause"])
    nodes = event.get("nodes") or []
    return str(nodes[0]) if nodes else None


def compare(truth: dict[str, Any], chain: dict[str, Any]) -> dict[str, Any]:
    tn = truth_nodes(truth)
    pn = pred_nodes(chain)
    inter = tn & pn
    node_recall = len(inter) / len(tn) if tn else 0.0
    node_precision = len(inter) / len(pn) if pn else 0.0
    t_root = truth_root(truth)
    p_root = chain.get("root_cause")
    root_hit = 1.0 if t_root and p_root and t_root == p_root else 0.0
    t_stages = len(truth.get("stages") or [])
    p_stages = len(chain.get("stages") or [])
    stage_ratio = min(1.0, p_stages / t_stages) if t_stages else 0.0
    return {
        "node_recall": node_recall,
        "node_precision": node_precision,
        "root_hit": root_hit,
        "stage_ratio": stage_ratio,
        "truth_nodes": sorted(tn),
        "pred_nodes": sorted(pn),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"chains": 0, "node_recall": 0.0, "node_precision": 0.0,
                "root_cause_top1": 0.0, "stage_ratio": 0.0}
    return {
        "chains": len(rows),
        "node_recall": round(mean(r["node_recall"] for r in rows), 4),
        "node_precision": round(mean(r["node_precision"] for r in rows), 4),
        "root_cause_top1": round(mean(r["root_hit"] for r in rows), 4),
        "stage_ratio": round(mean(r["stage_ratio"] for r in rows), 4),
    }
