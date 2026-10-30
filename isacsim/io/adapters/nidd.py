"""把原生观测流导出为现有 5G-A-NIDD 原型可消费的形态。

产物：
- d0.jsonl / d1.jsonl / d2.jsonl / d3a.jsonl / d3b.jsonl / d3c.jsonl
- d4.jsonl（核心网）/ d5.jsonl（平台）/ defense.jsonl
- chain.json（链构建器输出）
- cam.pcapng
- labels.json / ground-truth.csv
- manifest.json
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from ..pcapng import PcapngWriter


def _iter_jsonl(run_dir: Path, stream: str) -> Iterable[dict[str, Any]]:
    import gzip

    plain = run_dir / f"{stream}.jsonl"
    gz = run_dir / f"{stream}.jsonl.gz"
    opener = None
    if plain.exists():
        opener = plain.open("r", encoding="utf-8")
    elif gz.exists():
        opener = gzip.open(gz, "rt", encoding="utf-8")
    if opener is None:
        return
    with opener as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def export_nidd(run_dir: str | Path, out_dir: str | Path) -> dict[str, Any]:
    run = Path(run_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    def write_jsonl(name: str, rows: Iterable[dict]) -> None:
        n = 0
        with (out / name).open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
        counts[name] = n

    # D0 点迹
    def d0_rows():
        for rec in _iter_jsonl(run, "sensing.radar.track"):
            p = rec["payload"]
            yield {
                "ts": rec["ts_ns"], "frame_seq": p["frame_seq"],
                "sensor_id": p["sensor_id"], "target_id": p["target_id"],
                "range_m": p["range_m"], "radial_vel_mps": p["radial_vel_mps"],
                "azimuth_deg": p["azimuth_deg"], "elevation_deg": p["elevation_deg"],
                "rcs_dbsm": p["rcs_dbsm"], "snr_db": p["snr_db"],
                "confidence": p["confidence"],
            }
    write_jsonl("d0.jsonl", d0_rows())

    # D1 目标列表
    def d1_rows():
        for rec in _iter_jsonl(run, "sensing.objects"):
            p = rec["payload"]
            yield {
                "ts": rec["ts_ns"], "frame_seq": p["frame_seq"],
                "source": p["sensor_id"], "coverage_id": p["coverage_id"],
                "objects": p["objects"],
            }
    write_jsonl("d1.jsonl", d1_rows())

    # D2 帧
    def d2_rows():
        for rec in _iter_jsonl(run, "sensing.frame"):
            yield {"ts": rec["ts_ns"], **rec["payload"]}
    write_jsonl("d2.jsonl", d2_rows())

    # D3a/b/c
    def gnss_rows():
        for rec in _iter_jsonl(run, "sensing.gnss"):
            yield {"ts": rec["ts_ns"], **rec["payload"]}
    write_jsonl("d3a.jsonl", gnss_rows())

    def ctl_rows():
        for rec in _iter_jsonl(run, "control.command"):
            yield {"ts": rec["ts_ns"], **rec["payload"]}
    write_jsonl("d3b.jsonl", ctl_rows())

    def task_rows():
        for rec in _iter_jsonl(run, "task.state"):
            yield {"ts": rec["ts_ns"], **rec["payload"]}
    write_jsonl("d3c.jsonl", task_rows())

    # D4 核心网
    core_streams = ("core.nas", "core.session", "core.qos",
                    "core.handover", "core.upf", "core.nef")

    def d4_rows():
        for stream in core_streams:
            for rec in _iter_jsonl(run, stream):
                yield {"ts": rec["ts_ns"], "stream": stream, **rec["payload"]}
    write_jsonl("d4.jsonl", d4_rows())

    # D5 平台
    app_streams = ("app.auth", "app.ingest", "app.subscription",
                   "app.control", "app.audit")

    def d5_rows():
        for stream in app_streams:
            for rec in _iter_jsonl(run, stream):
                yield {"ts": rec["ts_ns"], "stream": stream, **rec["payload"]}
    write_jsonl("d5.jsonl", d5_rows())

    # 防御动作
    def defense_rows():
        for rec in _iter_jsonl(run, "defense.action"):
            yield {"ts": rec["ts_ns"], **rec["payload"]}
    write_jsonl("defense.jsonl", defense_rows())

    # 链输出
    chain_path = run / "chains.json"
    if chain_path.exists():
        try:
            chains = json.loads(chain_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            chains = []
        (out / "chain.json").write_text(json.dumps(chains, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        counts["chain"] = len(chains)

    # CAM → pcapng
    with PcapngWriter(out / "cam.pcapng") as w:
        for rec in _iter_jsonl(run, "v2x.cam"):
            p = rec["payload"]
            body = json.dumps({"type": "cam", **p}, ensure_ascii=False).encode()
            src_ip = f"10.0.0.{abs(hash(p['sender_pseudo'])) % 200 + 10}"
            w.write(rec["ts_ns"], body, src_ip=src_ip)
    counts["cam.pcapng"] = w.count

    # 真值与标签
    truth_path = run / "truth" / "events.jsonl"
    events = [json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines() if line] if truth_path.exists() else []
    (out / "labels.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "ground-truth.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["event_id", "campaign", "official_type", "t0", "t1", "nodes", "label"])
        for ev in events:
            writer.writerow([ev["event_id"], ev["campaign"], ev["official_type"],
                             ev["t0"], ev["t1"], ";".join(ev.get("nodes", [])), ev.get("label", 1)])
    counts["labels"] = len(events)

    (out / "manifest.json").write_text(json.dumps({
        "source_run": str(run), "adapter": "nidd-v1",
        "counts": counts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts
