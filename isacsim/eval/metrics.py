"""事件级评估：告警聚类、真值匹配、指标与置信区间。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .chain_metrics import aggregate as aggregate_chains, compare as compare_chain


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


@dataclass
class DetectedEvent:
    t0: float
    t1: float
    rules: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)
    n_alerts: int = 0


def cluster_alerts(alerts: list[dict], gap: float = 2.5) -> list[DetectedEvent]:
    """按时间间隔把告警聚成"疑似事件"。"""
    if not alerts:
        return []
    ordered = sorted(alerts, key=lambda a: a["t"])
    events: list[DetectedEvent] = []
    cur = DetectedEvent(t0=ordered[0]["t"], t1=ordered[0]["t"],
                        rules={ordered[0]["rule"]}, entities={ordered[0].get("entity", "")},
                        n_alerts=1)
    for a in ordered[1:]:
        if a["t"] - cur.t1 <= gap:
            cur.t1 = a["t"]
            cur.rules.add(a["rule"])
            cur.entities.add(a.get("entity", ""))
            cur.n_alerts += 1
        else:
            events.append(cur)
            cur = DetectedEvent(t0=a["t"], t1=a["t"], rules={a["rule"]},
                                entities={a.get("entity", "")}, n_alerts=1)
    events.append(cur)
    return events


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def match_events(truth: list[dict], detected: list[DetectedEvent],
                 pad: float = 1.0) -> tuple[int, list[int], list[int]]:
    """返回（命中数, 命中真值索引, 未匹配事件索引）。一对一贪心。"""
    used: set[int] = set()
    hit_idx: list[int] = []
    for i, ev in enumerate(truth):
        t0, t1 = ev["t0"], ev["t1"]
        best = -1
        best_ov = 0.0
        for j, d in enumerate(detected):
            if j in used:
                continue
            ov = overlap(t0, t1, d.t0 - pad, d.t1 + pad)
            if ov > best_ov:
                best_ov, best = ov, j
        if best >= 0 and best_ov > 0:
            used.add(best)
            hit_idx.append(i)
    unmatched = [j for j in range(len(detected)) if j not in used]
    return len(hit_idx), hit_idx, unmatched


def evaluate_runs(run_dirs: list[Path]) -> dict[str, Any]:
    """对一批运行目录做事件级统计。

    每个运行目录需包含：truth/events.jsonl、alerts.jsonl、meta.json。
    """
    attack_events = 0
    hits = 0
    normal_runs = 0
    normal_with_alerts = 0
    by_type: dict[str, dict[str, int]] = {}
    latencies: list[float] = []
    per_run: list[dict[str, Any]] = []
    chain_rows: list[dict[str, Any]] = []

    for run in run_dirs:
        truth_path = run / "truth" / "events.jsonl"
        alerts_path = run / "alerts.jsonl"
        meta_path = run / "meta.json"
        truth = []
        if truth_path.exists():
            truth = [json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        alerts = []
        if alerts_path.exists():
            alerts = [json.loads(line) for line in alerts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        chains: list[dict[str, Any]] = []
        chain_path = run / "chains.json"
        if chain_path.exists():
            try:
                chains = json.loads(chain_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                chains = []
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        detected = cluster_alerts(alerts)
        latencies.extend(a.get("latency_ms", 0.0) for a in alerts if a.get("latency_ms"))

        if meta.get("kind") == "normal":
            normal_runs += 1
            fp = 1 if detected else 0
            normal_with_alerts += fp
            per_run.append({"run": run.name, "kind": "normal", "fp": fp,
                            "alerts": len(alerts), "events": len(detected)})
            continue

        attack_events += len(truth)
        n_hit, hit_idx, unmatched = match_events(truth, detected)
        hits += n_hit
        run_chain_rows: list[dict[str, Any]] = []
        for i, ev in enumerate(truth):
            t = ev["campaign"]
            slot = by_type.setdefault(t, {"n": 0, "hit": 0})
            slot["n"] += 1
            if i in hit_idx:
                slot["hit"] += 1
            best = None
            best_ov = 0.0
            for chain in chains:
                ov = overlap(ev["t0"], ev["t1"], chain.get("t0", 0.0),
                             chain.get("t1", 0.0))
                if ov >= best_ov:
                    best_ov, best = ov, chain
            row = compare_chain(ev, best or {"nodes": [], "stages": [], "root_cause": None})
            row["campaign"] = t
            run_chain_rows.append(row)
            chain_rows.append(row)
        per_run.append({"run": run.name, "kind": "attack", "truth": len(truth),
                        "hit": n_hit, "detected": len(detected),
                        "unmatched_detections": len(unmatched), "alerts": len(alerts),
                        "chains": len(chains),
                        "chain_node_recall": round(
                            sum(r["node_recall"] for r in run_chain_rows) /
                            max(1, len(run_chain_rows)), 4)})

    recall = hits / attack_events if attack_events else 0.0
    fpr = normal_with_alerts / normal_runs if normal_runs else 0.0
    r_lo, r_hi = wilson(hits, attack_events) if attack_events else (0.0, 1.0)
    f_lo, f_hi = wilson(normal_with_alerts, normal_runs) if normal_runs else (0.0, 1.0)
    lat_sorted = sorted(latencies)

    def q(p: float) -> float:
        if not lat_sorted:
            return 0.0
        return round(lat_sorted[min(len(lat_sorted) - 1, int(p * (len(lat_sorted) - 1)))], 3)

    return {
        "runs": len(run_dirs),
        "attack_events": attack_events,
        "hits": hits,
        "recall": {"point": round(recall, 4), "lower95": round(r_lo, 4), "upper95": round(r_hi, 4)},
        "normal_runs": normal_runs,
        "false_positive_runs": normal_with_alerts,
        "fpr": {"point": round(fpr, 4), "lower95": round(f_lo, 4), "upper95": round(f_hi, 4)},
        "detect_latency_ms": {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "n": len(lat_sorted)},
        "by_type": by_type,
        "chain": aggregate_chains(chain_rows),
        "per_run": per_run,
    }


def render_report(result: dict[str, Any]) -> str:
    r = result["recall"]
    f = result["fpr"]
    lat = result["detect_latency_ms"]
    lines = [
        "# 事件级评估报告",
        "",
        f"- 运行数：{result['runs']}（攻击事件 {result['attack_events']}，正常运行 {result['normal_runs']}）",
        f"- 识别率：{r['point']:.2%}（95% CI {r['lower95']:.2%} – {r['upper95']:.2%}，命中 {result['hits']}/{result['attack_events']}）",
        f"- 误检率：{f['point']:.2%}（95% CI {f['lower95']:.2%} – {f['upper95']:.2%}，误报 {result['false_positive_runs']}/{result['normal_runs']}）",
        f"- 检测时延（ingest→decide）：P50 {lat['p50']} ms / P95 {lat['p95']} ms / P99 {lat['p99']} ms（n={lat['n']}）",
    ]
    chain = result.get("chain") or {}
    if chain.get("chains"):
        lines.append(
            f"- 链级：节点召回 {chain['node_recall']:.2%} / 精确率 {chain['node_precision']:.2%} / "
            f"根因 top-1 {chain['root_cause_top1']:.2%} / 阶段覆盖 {chain['stage_ratio']:.2%} "
            f"（{chain['chains']} 条）"
        )
    lines.extend([
        "",
        "| 类型 | 事件数 | 命中 | 识别率 |",
        "|---|---|---|---|",
    ])
    for k in sorted(result["by_type"]):
        v = result["by_type"][k]
        lines.append(f"| {k} | {v['n']} | {v['hit']} | {v['hit'] / max(1, v['n']):.2%} |")
    return "\n".join(lines) + "\n"
