"""Web 端历史数据清理：扫描、策略预览、后台执行。

复用 `isacsim.cleanup` 的选择与安全规则，额外提供：
- 面向页面的摘要/预览结构；
- 后台删除任务与进度（大目录不只卡住一个 HTTP 请求）；
- 当前正在运行的 Web 会话始终受保护。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ..cleanup import (
    Artifact,
    CleanupPlan,
    build_plan,
    directory_size,
    human_size,
    remove_artifact,
)

DEFAULT_KEEP = 3
MAX_KEEP = 999
SCOPES = ("web", "batch")


class StorageError(ValueError):
    """清理参数不合法。"""


class StorageBusy(StorageError):
    """已有清理任务在执行。"""


def normalize_policy(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    raw_scope = body.get("scope", ["web"])
    if isinstance(raw_scope, str):
        raw_scope = [raw_scope]
    scope = [s for s in raw_scope if s in SCOPES]
    if not scope:
        raise StorageError("清理范围不能为空")
    try:
        keep = int(body.get("keep", DEFAULT_KEEP))
    except (TypeError, ValueError) as exc:
        raise StorageError("保留数量必须是整数") from exc
    if keep < 0 or keep > MAX_KEEP:
        raise StorageError(f"保留数量需在 0–{MAX_KEEP} 之间")

    older = body.get("older_than_days")
    if older in (None, "", 0):
        older = None
    else:
        try:
            older = float(older)
        except (TypeError, ValueError) as exc:
            raise StorageError("天数必须是数字") from exc
        if older <= 0:
            raise StorageError("天数必须大于 0")

    return {
        "scope": scope,
        "keep": keep,
        "older_than_days": older,
        "all": bool(body.get("all", False)),
    }


def _count_runs(path: Path) -> int:
    """批量根目录下带 meta.json 或 truth/events.jsonl 的子目录数；单次运行算 1。"""
    n = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and (
                    (Path(entry.path) / "meta.json").is_file()
                    or (Path(entry.path) / "truth" / "events.jsonl").is_file()):
                    n += 1
    except OSError:
        return 0
    if n:
        return n
    return 1 if (path / "meta.json").is_file() or (path / "truth" / "events.jsonl").is_file() else 0


class StorageService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._job: dict[str, Any] = {
            "state": "idle", "total": 0, "done": 0, "removed": 0,
            "freed_bytes": 0, "errors": [], "started_at": None,
            "finished_at": None, "policy": None, "planned_bytes": 0,
        }
        self._cache: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    def _scan(self) -> list[Artifact]:
        """扫描全部可清理产物（不套策略）。"""
        plan = build_plan(self.root, keep=0, include_web=True, include_batches=True,
                          all_=True, protect_active=False)
        return list(plan.candidates) + list(plan.kept)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < 5.0:
                return dict(self._cache[1])
        out_bytes = directory_size(self.root) if self.root.is_dir() else 0
        web = batch = 0
        web_bytes = batch_bytes = 0
        newest = oldest = None
        if self.root.is_dir():
            for art in self._scan():
                if art.kind == "web":
                    web += 1
                    web_bytes += art.size_bytes
                else:
                    batch += 1
                    batch_bytes += art.size_bytes
                newest = art.mtime if newest is None else max(newest, art.mtime)
                oldest = art.mtime if oldest is None else min(oldest, art.mtime)
        try:
            usage = shutil.disk_usage(self.root)
            disk = {"free_bytes": usage.free, "total_bytes": usage.total,
                    "used_bytes": usage.used}
        except OSError:
            disk = {"free_bytes": 0, "total_bytes": 0, "used_bytes": 0}
        payload = {
            "root": str(self.root),
            "out_bytes": int(out_bytes),
            "out_human": human_size(out_bytes),
            "disk": disk,
            "web": {"count": web, "bytes": web_bytes, "human": human_size(web_bytes)},
            "batch": {"count": batch, "bytes": batch_bytes, "human": human_size(batch_bytes)},
            "newest_mtime": newest,
            "oldest_mtime": oldest,
        }
        with self._lock:
            self._cache = (time.monotonic(), payload)
        return dict(payload)

    # ------------------------------------------------------------------
    def _build(self, policy: dict[str, Any], protect: Iterable[str | Path]) -> CleanupPlan:
        if not self.root.is_dir():
            raise StorageError(f"产物目录不存在：{self.root}")
        return build_plan(
            self.root,
            keep=policy["keep"],
            older_than_days=policy["older_than_days"],
            include_web="web" in policy["scope"],
            include_batches="batch" in policy["scope"],
            all_=policy["all"],
            protect=list(protect),
            # Web 端知道当前会话（MANAGER.active_run_dir）；不再用 web.pid
            # 兜底保护最新目录，否则“清空”总会莫名留下一个。
            protect_active=False,
        )

    def _item(self, art: Artifact, action: str) -> dict[str, Any]:
        rel = art.path.relative_to(self.root).as_posix()
        return {
            "id": rel,
            "name": art.path.name,
            "kind": art.kind,
            "path": rel,
            "size_bytes": art.size_bytes,
            "size_human": human_size(art.size_bytes),
            "mtime": round(art.mtime, 3),
            "action": action,
            "reason": art.reason,
            "protected": bool(art.protected),
            "runs": _count_runs(art.path),
        }

    def plan(self, body: dict[str, Any] | None,
             protect: Iterable[str | Path] = ()) -> dict[str, Any]:
        policy = normalize_policy(body)
        plan = self._build(policy, protect)
        items = [self._item(a, "delete") for a in plan.candidates]
        keeps = [self._item(a, "keep") for a in plan.kept]
        items.sort(key=lambda x: (-x["size_bytes"], x["name"]))
        keeps.sort(key=lambda x: (-x["mtime"], x["name"]))
        return {
            "policy": policy,
            "delete_count": len(items),
            "delete_bytes": plan.reclaim_bytes,
            "delete_human": human_size(plan.reclaim_bytes),
            "keep_count": len(keeps),
            "protected_count": len(plan.protected),
            "items": items,
            "kept": keeps,
            "summary": self.summary(),
        }

    # ------------------------------------------------------------------
    def start(self, body: dict[str, Any] | None,
              protect: Iterable[str | Path] = ()) -> dict[str, Any]:
        policy = normalize_policy(body)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise StorageBusy("已有清理任务在运行")
            plan = self._build(policy, protect)
            if not plan.candidates:
                raise StorageError("没有符合条件的历史数据")
            self._job = {
                "state": "running",
                "total": len(plan.candidates),
                "done": 0,
                "removed": 0,
                "freed_bytes": 0,
                "errors": [],
                "started_at": time.time(),
                "finished_at": None,
                "policy": policy,
                "planned_bytes": plan.reclaim_bytes,
            }
            self._cache = None
            self._thread = threading.Thread(target=self._run, args=(plan,),
                                            name="isacsim-cleanup", daemon=True)
            self._thread.start()
            return self.status()

    def _run(self, plan: CleanupPlan) -> None:
        try:
            for art in plan.candidates:
                ok, err = remove_artifact(art, self.root)
                with self._lock:
                    self._job["done"] += 1
                    if ok:
                        self._job["removed"] += 1
                        self._job["freed_bytes"] += art.size_bytes
                    elif err:
                        self._job["errors"].append(err)
        except Exception as exc:  # noqa: BLE001 - 后台线程不能静默死掉
            with self._lock:
                self._job["errors"].append(f"任务中断：{exc}")
        finally:
            with self._lock:
                self._job["state"] = "done"
                self._job["finished_at"] = time.time()
                self._cache = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = dict(self._job)
            job["errors"] = list(self._job["errors"][-20:])
            job["freed_human"] = human_size(self._job["freed_bytes"])
            job["planned_human"] = human_size(self._job["planned_bytes"])
        job["running"] = bool(self._thread is not None and self._thread.is_alive())
        try:
            usage = shutil.disk_usage(self.root)
            job["disk_free_bytes"] = usage.free
            job["disk_free_human"] = human_size(usage.free)
        except OSError:
            job["disk_free_bytes"] = 0
            job["disk_free_human"] = "0 B"
        return job

    def wait(self, timeout: float = 30.0) -> dict[str, Any]:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()
