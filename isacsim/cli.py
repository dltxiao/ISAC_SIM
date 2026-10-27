"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import GENERATOR_VERSION
from .attacks.base import AttackSpec
from .config import default_scene, load_scene
from .core.engine import Simulation
from .core.preflight import check_scene, estimate_batch, hardware_info


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_info(args) -> int:
    from . import sumo_env

    hw = hardware_info()
    try:
        env = sumo_env.ensure()
        hw["sumo"] = {"version": env.version, "home": str(env.home)}
    except Exception as exc:  # noqa: BLE001
        hw["sumo"] = {"error": str(exc)}
    scene = default_scene()
    hw["default_estimate"] = check_scene(scene).as_dict()
    hw["generator_version"] = GENERATOR_VERSION
    _print(hw)
    return 0


def cmd_bench(args) -> int:
    """短跑一次，标定本机实时倍率并写入 .isacsim_bench.json。"""
    scene = default_scene(seed=7)
    scene.duration_s = args.seconds
    sim = Simulation(scene, run_id="bench", out_dir=None)
    t0 = time.perf_counter()
    sim.start()
    n = 0
    while sim.step_once():
        n += 1
    wall = time.perf_counter() - t0
    sim.stop()
    factor = scene.duration_s / wall if wall else 0.0
    payload = {"realtime_factor": round(factor, 1), "wall_s": round(wall, 2),
               "sim_s": scene.duration_s, "vehicles": sim.stats.vehicles,
               "steps": n, "records": sim.stats.records_total,
               "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    Path(".isacsim_bench.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print(payload)
    return 0


def cmd_preflight(args) -> int:
    scene = load_scene(args.config) if args.config else default_scene()
    res = check_scene(scene)
    _print(res.as_dict())
    return 0 if res.ok else 2


def cmd_run(args) -> int:
    scene = load_scene(args.config) if args.config else default_scene(seed=args.seed)
    if args.duration:
        scene.duration_s = args.duration
    scene.seed = args.seed
    out = Path(args.out) / f"{scene.name}-{args.seed}-{int(time.time())}"
    from .detect.reference import ReferenceDetector

    detector = ReferenceDetector(scene) if not args.no_detect else None
    sim = Simulation(scene, out_dir=out, detector=detector, realtime=args.realtime,
                     speed=args.speed)
    if args.attack:
        for t in args.attack.split(","):
            sim.scheduler.submit(AttackSpec(type=t.strip(), amplitude=args.amplitude,
                                            duration=args.attack_duration,
                                            start=args.attack_start))
    print(f"[isacsim] 运行目录：{out}")
    sim.run()
    if detector is not None:
        detector.dump(out / "alerts.jsonl")
        detector.dump_chains(out / "chains.json")
    snap = sim.snapshot()
    _print({k: snap[k] for k in ("state", "sim_time", "records_total", "bytes_total",
                                 "attacks", "truth_events")})
    print(f"检测时延：{snap.get('detector', {}).get('latency_ms')}")
    return 0


def cmd_batch(args) -> int:
    from .batch.runner import build_jobs, run_jobs

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    jobs = build_jobs(args.out, attacks=args.attacks, normal=args.normal,
                      types=types, duration=args.duration, amplitude=args.amplitude,
                      split=args.split, streams=not args.no_streams, seed0=args.seed)
    est = estimate_batch(len(jobs), default_scene(), args.workers)
    print(f"[isacsim] 计划 {len(jobs)} 个场景，{args.workers} 进程；预估 {est}")
    t0 = time.perf_counter()

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"  进度 {done}/{total}", flush=True)

    results = run_jobs(jobs, workers=args.workers, progress=progress)
    payload = {"jobs": len(jobs), "wall_s": round(time.perf_counter() - t0, 1),
               "results": results[:20], "out": str(Path(args.out))}
    (Path(args.out) / "batch.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    _print({k: payload[k] for k in ("jobs", "wall_s", "out")})
    return 0


def cmd_eval(args) -> int:
    from .eval.metrics import evaluate_runs, render_report

    root = Path(args.runs)
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / "meta.json").exists())
    if not dirs:
        print(f"没有找到运行目录：{root}")
        return 2
    result = evaluate_runs(dirs)
    report = render_report(result)
    out = Path(args.report) if args.report else root / "report.md"
    out.write_text(report, encoding="utf-8")
    (root / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(report)
    return 0


def cmd_export(args) -> int:
    from .io.adapters.nidd import export_nidd

    counts = export_nidd(args.input, args.out)
    _print({"counts": counts, "out": args.out})
    return 0


def cmd_web(args) -> int:
    from .web.api import serve

    serve(host=args.host, port=args.port, scene_name=args.scene, open_browser=args.open)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isacsim", description="车联网跨层攻击模拟环境")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="硬件、SUMO 与容量包络")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("bench", help="本机实时倍率标定")
    p.add_argument("--seconds", type=float, default=10.0)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("preflight", help="算力预检")
    p.add_argument("-c", "--config", default=None)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("run", help="运行单个场景")
    p.add_argument("-c", "--config", default=None)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--out", default="out")
    p.add_argument("-a", "--attack", default=None, help="A1–A10 或原语，逗号分隔")
    p.add_argument("--attack-duration", type=float, default=10.0)
    p.add_argument("--attack-start", type=float, default=3.0)
    p.add_argument("--amplitude", type=float, default=0.6)
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--no-detect", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("batch", help="批量生成数据集")
    p.add_argument("--out", default="out/ds")
    p.add_argument("--attacks", type=int, default=16)
    p.add_argument("--normal", type=int, default=16)
    p.add_argument("--types", default="A1,A2,A3,A4")
    p.add_argument("--duration", type=float, default=20.0)
    p.add_argument("--amplitude", type=float, default=0.6)
    p.add_argument("--split", default="train")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--no-streams", action="store_true")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("eval", help="事件级评估")
    p.add_argument("--runs", default="out/ds")
    p.add_argument("--report", default=None)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("export", help="导出为 5G-A-NIDD 兼容格式")
    p.add_argument("format", nargs="?", default="nidd", choices=["nidd"])
    p.add_argument("-i", "--input", required=True, help="运行目录")
    p.add_argument("-o", "--out", required=True, help="输出目录")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("web", help="启动 Web 控制台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--scene", default="arterial")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_web)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
