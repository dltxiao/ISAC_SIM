import os
import time

from isacsim.cleanup import apply_plan, build_plan, human_size
from isacsim.cli import main


def _web_run(root, name: str, days_ago: float) -> None:
    path = root / "web" / name
    path.mkdir(parents=True)
    (path / "truth").mkdir()
    (path / "truth" / "events.jsonl").write_text("", encoding="utf-8")
    ts = time.time() - days_ago * 86400
    os.utime(path, (ts, ts))


def _batch_root(root, name: str, runs: int = 1) -> None:
    path = root / name
    for i in range(runs):
        child = path / f"train-{i:04d}-A1"
        child.mkdir(parents=True)
        (child / "meta.json").write_text("{}", encoding="utf-8")


def test_plan_keeps_newest_web_runs(tmp_path):
    for i, days in enumerate((1, 2, 3, 4), start=1):
        _web_run(tmp_path, f"web-{i}", days)
    _batch_root(tmp_path, "ds_old", runs=2)

    plan = build_plan(tmp_path, keep=2, protect_active=False)
    assert len(plan.candidates) == 2
    assert {a.path.name for a in plan.candidates} == {"web-3", "web-4"}
    assert len(plan.kept) == 2
    assert all(a.kind == "web" for a in plan.candidates)


def test_batches_are_opt_in_and_exports_ignored(tmp_path):
    _batch_root(tmp_path, "ds_old", runs=2)
    export = tmp_path / "nidd_old"
    export.mkdir()
    (export / "d0.jsonl").write_text("", encoding="utf-8")

    plan = build_plan(tmp_path, keep=0, protect_active=False)
    assert plan.candidates == []

    plan = build_plan(tmp_path, keep=0, include_batches=True, protect_active=False)
    assert [a.path.name for a in plan.candidates] == ["ds_old"]
    assert plan.candidates[0].size_bytes > 0


def test_older_than_and_protect(tmp_path):
    for i, days in enumerate((1, 2, 3, 4), start=1):
        _web_run(tmp_path, f"web-{i}", days)

    plan = build_plan(tmp_path, keep=1, older_than_days=2, protect_active=False,
                      protect=[tmp_path / "web" / "web-4"])
    assert {a.path.name for a in plan.candidates} == {"web-2", "web-3"}
    assert {a.path.name for a in plan.protected} == {"web-4"}


def test_active_web_pid_protects_newest(tmp_path):
    _web_run(tmp_path, "web-new", 1)
    _web_run(tmp_path, "web-old", 2)
    (tmp_path / "web.pid").write_text(str(os.getpid()), encoding="utf-8")

    plan = build_plan(tmp_path, all_=True)
    assert [a.path.name for a in plan.candidates] == ["web-old"]
    assert [a.path.name for a in plan.protected] == ["web-new"]


def test_apply_removes_only_candidates(tmp_path):
    _web_run(tmp_path, "web-keep", 1)
    _web_run(tmp_path, "web-drop-1", 2)
    _web_run(tmp_path, "web-drop-2", 3)

    plan = build_plan(tmp_path, keep=1, protect_active=False)
    result = apply_plan(plan)
    assert result.removed == 2
    assert result.freed_bytes == plan.reclaim_bytes
    assert (tmp_path / "web" / "web-keep").is_dir()
    assert not (tmp_path / "web" / "web-drop-1").exists()
    assert not (tmp_path / "web" / "web-drop-2").exists()


def test_cli_preview_then_delete(tmp_path, capsys):
    _web_run(tmp_path, "web-keep", 1)
    _web_run(tmp_path, "web-drop", 2)

    assert main(["clean", "--out", str(tmp_path), "--keep", "1"]) == 0
    assert "预览" in capsys.readouterr().out
    assert (tmp_path / "web" / "web-drop").is_dir()

    assert main(["clean", "--out", str(tmp_path), "--keep", "1", "--yes"]) == 0
    assert "已删除 1 个目录" in capsys.readouterr().out
    assert (tmp_path / "web" / "web-keep").is_dir()
    assert not (tmp_path / "web" / "web-drop").exists()


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KiB"
    assert human_size(1024 ** 3) == "1.0 GiB"
