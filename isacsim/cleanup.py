"""开发阶段运行产物清理：默认只清 Web 会话，批量数据集需显式开启。

设计原则：
- 默认 dry-run，必须 `--yes` 才真正删除；
- 只删 `out/` 下的运行目录，不碰源码、配置和导出数据；
- Web 会话按修改时间保留最近 N 个；
- 批量/实验目录整目录保留最近 N 个，避免误删单条运行后留下不一致的 report/eval；
- 保护正在写入的 Web 会话和用户显式指定的路径。
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

DAY_S = 86400.0


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: str                 # web / batch
    size_bytes: int
    mtime: float
    protected: bool = False
    reason: str = ""


@dataclass
class CleanupPlan:
    root: Path
    candidates: list[Artifact]
    kept: list[Artifact]
    protected: list[Artifact]

    @property
    def reclaim_bytes(self) -> int:
        return sum(a.size_bytes for a in self.candidates)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.candidates:
            out[a.kind] = out.get(a.kind, 0) + 1
        return out


@dataclass
class CleanupResult:
    removed: int
    freed_bytes: int
    errors: list[str]


def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TiB"


def directory_size(path: Path) -> int:
    """不跟随符号链接地统计目录大小。"""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _is_run_dir(path: Path) -> bool:
    return (path / "meta.json").is_file() or (path / "truth" / "events.jsonl").is_file()


def _has_run_child(path: Path) -> bool:
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError:
        return False
    return any(
        e.is_dir(follow_symlinks=False) and (Path(e.path) / "meta.json").is_file()
        for e in children
    )


def _artifact(path: Path, kind: str) -> Artifact:
    st = path.stat()
    return Artifact(path=path, kind=kind, size_bytes=directory_size(path), mtime=st.st_mtime)


def _web_artifacts(root: Path) -> list[Artifact]:
    base = root / "web"
    if not base.is_dir():
        return []
    out: list[Artifact] = []
    with os.scandir(base) as it:
        for entry in it:
            if entry.is_dir(follow_symlinks=False):
                out.append(_artifact(Path(entry.path), "web"))
    return out


def _batch_artifacts(root: Path) -> list[Artifact]:
    """识别 out/ 下的单次运行或批量实验根目录。

    批量根目录含一个或多个带 meta.json 的子目录；导出目录（nidd_*）、
    日志和截图不会被当作运行产物。
    """
    out: list[Artifact] = []
    with os.scandir(root) as it:
        for entry in it:
            if not entry.is_dir(follow_symlinks=False) or entry.name == "web":
                continue
            path = Path(entry.path)
            if _is_run_dir(path) or _has_run_child(path):
                out.append(_artifact(path, "batch"))
    return out


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _active_web_run(root: Path, web: list[Artifact]) -> Artifact | None:
    """有 Web 服务在运行时，保护最新会话，避免删掉正在写入的目录。"""
    if not web:
        return None
    pid_file = root / "web.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if not _pid_alive(pid):
        return None
    return max(web, key=lambda a: (a.mtime, a.path.name))


def _safe_target(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved != base and resolved.is_relative_to(base) and not path.is_symlink()


def build_plan(
    root: str | Path = "out",
    *,
    keep: int = 3,
    older_than_days: float | None = None,
    include_batches: bool = False,
    all_: bool = False,
    protect: Iterable[str | Path] = (),
    protect_active: bool = True,
) -> CleanupPlan:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"目录不存在：{root}")
    if keep < 0:
        raise ValueError("--keep 不能为负数")
    if older_than_days is not None and older_than_days <= 0:
        raise ValueError("--older-than 必须为正数")

    artifacts = _web_artifacts(root)
    if include_batches:
        artifacts.extend(_batch_artifacts(root))

    reasons: dict[Path, str] = {}
    for raw in protect:
        pp = Path(raw).expanduser()
        if not pp.is_absolute():
            pp = Path.cwd() / pp
        pp = pp.resolve()
        for a in artifacts:
            if a.path == pp or pp in a.path.parents:
                reasons.setdefault(a.path, f"手动保护：{raw}")

    if protect_active:
        active = _active_web_run(root, [a for a in artifacts if a.kind == "web"])
        if active is not None:
            reasons.setdefault(active.path, f"Web 服务运行中：{root / 'web.pid'}")

    protected = [replace(a, protected=True, reason=reasons[a.path])
                 for a in artifacts if a.path in reasons]

    candidates: list[Artifact] = []
    kept: list[Artifact] = []
    now = time.time()

    def keep_reason(a: Artifact, index: int) -> str | None:
        if a.path in reasons:
            return reasons[a.path]
        if all_:
            return None
        if index < keep:
            return f"保留最新 {keep} 个"
        if older_than_days is not None:
            age_days = max(0.0, (now - a.mtime) / DAY_S)
            if age_days < older_than_days:
                return f"未超过 {older_than_days:g} 天"
        return None

    for kind in ("web", "batch"):
        group = sorted((a for a in artifacts if a.kind == kind),
                       key=lambda a: (-a.mtime, a.path.name))
        for index, a in enumerate(group):
            reason = keep_reason(a, index)
            if reason:
                kept.append(replace(a, protected=a.path in reasons, reason=reason))
            else:
                candidates.append(a)

    return CleanupPlan(root=root, candidates=candidates, kept=kept, protected=protected)


def apply_plan(plan: CleanupPlan) -> CleanupResult:
    """执行计划；只允许删除 out/ 下一层的 Web 会话或实验根目录。"""
    removed = 0
    freed = 0
    errors: list[str] = []
    allowed_parents = {plan.root, plan.root / "web"}
    for a in plan.candidates:
        if not _safe_target(a.path, plan.root) or a.path.parent not in allowed_parents:
            errors.append(f"跳过不安全的路径：{a.path}")
            continue
        if not a.path.is_dir():
            errors.append(f"目录已不存在：{a.path}")
            continue
        try:
            shutil.rmtree(a.path)
        except OSError as exc:
            errors.append(f"{a.path.name}: {exc}")
            continue
        removed += 1
        freed += a.size_bytes
    return CleanupResult(removed=removed, freed_bytes=freed, errors=errors)
