"""定位并初始化 SUMO 运行环境。

本机通过 `pip install eclipse-sumo` 安装 SUMO（无需 root），
其布局为 site-packages/sumo/{bin,tools}。TraCI 与 sumolib 位于 tools 目录，
需要手工加入 sys.path。
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class SumoNotFoundError(RuntimeError):
    """本机找不到可用的 SUMO 安装。"""


@dataclass(frozen=True)
class SumoEnv:
    home: Path
    bin_dir: Path
    tools_dir: Path
    version: str

    def binary(self, name: str = "sumo") -> Path:
        path = self.bin_dir / name
        if not path.exists():
            raise SumoNotFoundError(f"找不到 SUMO 可执行文件：{path}")
        return path


def _candidate_homes() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SUMO_HOME")
    if env:
        cands.append(Path(env))
    try:  # pip wheel 的 sumo 包
        import sumo  # type: ignore

        cands.append(Path(sumo.__file__).resolve().parent)
    except Exception:  # noqa: BLE001 - 安装缺失时继续尝试固定路径
        pass
    cands.extend([Path("/usr/share/sumo"), Path("/usr/local/share/sumo"), Path("/opt/sumo")])
    # 去重且保持顺序
    seen: set[Path] = set()
    out: list[Path] = []
    for c in cands:
        c = c.resolve() if c.exists() else c
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _read_version(binary: Path) -> str:
    try:
        proc = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=20
        )
        text = (proc.stdout or proc.stderr).strip().splitlines()
        return text[0] if text else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


@lru_cache(maxsize=1)
def ensure() -> SumoEnv:
    """初始化 SUMO 环境变量与导入路径，返回路径信息。"""
    for home in _candidate_homes():
        bin_dir = home / "bin"
        tools_dir = home / "tools"
        if (bin_dir / "sumo").exists() and (tools_dir / "traci").exists():
            os.environ["SUMO_HOME"] = str(home)
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if str(bin_dir) not in path_parts:
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            if str(tools_dir) not in sys.path:
                sys.path.insert(0, str(tools_dir))
            return SumoEnv(home=home, bin_dir=bin_dir, tools_dir=tools_dir,
                           version=_read_version(bin_dir / "sumo"))
    raise SumoNotFoundError(
        "未找到 SUMO。请执行：.venv/bin/pip install eclipse-sumo==1.28.0"
    )


def traci():
    """返回已初始化的 traci 模块。"""
    ensure()
    import traci as _traci  # type: ignore

    return _traci


def sumolib():
    ensure()
    import sumolib as _sumolib  # type: ignore

    return _sumolib
