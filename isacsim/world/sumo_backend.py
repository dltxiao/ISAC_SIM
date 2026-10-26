"""SUMO 后端：进程生命周期、TraCI 订阅、动态增删与执行器接口。"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import sumo_env


class SumoError(RuntimeError):
    """SUMO 启停或运行异常。"""


@dataclass(slots=True)
class VehicleState:
    vid: str
    x: float
    y: float
    angle: float          # SUMO 约定：北=0，顺时针
    speed: float
    length: float
    width: float
    type_id: str
    lane_id: str
    heading_deg: float    # 数学约定：东=0，逆时针
    vx: float
    vy: float
    cls: str = "car"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SumoBackend:
    def __init__(self, net: str | Path, route: str | Path, seed: int = 11,
                 step_s: float = 0.05, log_dir: str | Path | None = None,
                 port: int | None = None) -> None:
        self.net = str(Path(net).resolve())
        self.route = str(Path(route).resolve())
        self.seed = int(seed)
        self.step_s = float(step_s)
        self.port = port or _free_port()
        self.log_dir = Path(log_dir) if log_dir else None
        self.proc: subprocess.Popen | None = None
        self.traci = None
        self._subscribed: set[str] = set()
        self._states: dict[str, VehicleState] = {}
        self._log_fh = None
        self._closed = False

    # ------------------------------------------------------------------
    def _log_path(self) -> Path | None:
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / "sumo.log"

    def start(self, timeout: float = 30.0) -> None:
        env = sumo_env.ensure()
        binary = env.binary("sumo")
        cmd = [
            str(binary), "-n", self.net, "-r", self.route,
            "--seed", str(self.seed),
            "--step-length", f"{self.step_s:.3f}",
            "--no-step-log", "--no-warnings",
        ]
        log_path = self._log_path()
        if log_path:
            self._log_fh = log_path.open("wb")
            stdout = self._log_fh
        else:
            stdout = subprocess.DEVNULL
        self.traci = sumo_env.traci()
        try:
            # traci.start 负责启动进程、等待端口并注册默认连接（SUMO 只接受一个客户端）
            self.traci.start(cmd, port=self.port, numRetries=max(1, int(timeout * 2)),
                             stdout=stdout)
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise SumoError(f"启动 SUMO 失败：{exc}\n{self.tail_log()}") from exc
        conn = self.traci.getConnection()
        self.proc = getattr(conn, "_process", None)
        if self.proc is None:
            self.close()
            raise SumoError("无法获取 SUMO 进程句柄")

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def step(self) -> bool:
        """推进一个仿真步；返回仿真是否仍在继续。"""
        if not self.running:
            raise SumoError(self.tail_log() or "SUMO 进程已退出")
        self.traci.simulationStep()
        self._refresh()
        return self.traci.simulation.getMinExpectedNumber() > 0 or \
            bool(self.traci.vehicle.getIDList())

    def _refresh(self) -> None:
        traci = self.traci
        ids = traci.vehicle.getIDList()
        for vid in ids:
            if vid not in self._subscribed:
                try:
                    traci.vehicle.subscribe(vid, (
                        traci.constants.VAR_POSITION,
                        traci.constants.VAR_ANGLE,
                        traci.constants.VAR_SPEED,
                        traci.constants.VAR_TYPE,
                        traci.constants.VAR_LENGTH,
                        traci.constants.VAR_WIDTH,
                        traci.constants.VAR_LANE_ID,
                    ))
                    self._subscribed.add(vid)
                except Exception:  # noqa: BLE001
                    continue
        results = traci.vehicle.getAllSubscriptionResults()
        states: dict[str, VehicleState] = {}
        if not results:  # 某些版本在车辆离开后返回空，退回逐车查询
            for vid in ids:
                try:
                    x, y = traci.vehicle.getPosition(vid)
                    angle = traci.vehicle.getAngle(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    vtype = traci.vehicle.getTypeID(vid)
                    length = traci.vehicle.getLength(vid)
                    width = traci.vehicle.getWidth(vid)
                    lane = traci.vehicle.getLaneID(vid)
                except Exception:  # noqa: BLE001
                    continue
                states[vid] = self._make_state(vid, x, y, angle, speed, vtype, length, width, lane)
        else:
            for vid, data in results.items():
                pos = data.get(traci.constants.VAR_POSITION)
                if pos is None:
                    continue
                angle = float(data.get(traci.constants.VAR_ANGLE, 0.0))
                speed = float(data.get(traci.constants.VAR_SPEED, 0.0))
                vtype = str(data.get(traci.constants.VAR_TYPE, "car"))
                length = float(data.get(traci.constants.VAR_LENGTH, 4.5))
                width = float(data.get(traci.constants.VAR_WIDTH, 1.8))
                lane = str(data.get(traci.constants.VAR_LANE_ID, ""))
                states[vid] = self._make_state(vid, float(pos[0]), float(pos[1]), angle,
                                               speed, vtype, length, width, lane)
        # 清理已离开的订阅
        for vid in list(self._subscribed):
            if vid not in states:
                self._subscribed.discard(vid)
        self._states = states

    @staticmethod
    def _make_state(vid: str, x: float, y: float, angle: float, speed: float,
                    vtype: str, length: float, width: float, lane: str) -> VehicleState:
        heading = (90.0 - angle) % 360.0
        import math

        vx = speed * math.cos(math.radians(heading))
        vy = speed * math.sin(math.radians(heading))
        cls = {"truck": "truck", "bus": "truck", "trailer": "truck",
               "motorcycle": "motorcycle", "bicycle": "bicycle"}.get(vtype, "car")
        return VehicleState(vid=vid, x=x, y=y, angle=angle, speed=speed,
                            length=length, width=width, type_id=vtype,
                            lane_id=lane, heading_deg=heading, vx=vx, vy=vy, cls=cls)

    def vehicles(self) -> dict[str, VehicleState]:
        return self._states

    # ------------------------------------------------------------------
    def add_vehicle(self, vid: str, edges: list[str], type_id: str = "car") -> None:
        route_id = f"rt_{vid}"
        try:
            self.traci.route.add(route_id, edges)
        except Exception:  # noqa: BLE001 - 已存在则复用
            pass
        self.traci.vehicle.add(vid, route_id, typeID=type_id, depart="now")

    def remove_vehicle(self, vid: str) -> None:
        try:
            self.traci.vehicle.remove(vid)
        except Exception:  # noqa: BLE001
            pass

    def slow_down(self, vid: str, speed: float, duration: float) -> None:
        try:
            self.traci.vehicle.slowDown(vid, max(0.0, speed), max(0.0, duration))
        except Exception:  # noqa: BLE001
            pass

    def set_speed(self, vid: str, speed: float) -> None:
        try:
            self.traci.vehicle.setSpeed(vid, max(0.0, speed))
        except Exception:  # noqa: BLE001
            pass

    def signal_state(self, tls_id: str) -> str:
        try:
            return self.traci.trafficlight.getRedYellowGreenState(tls_id)
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------
    def net_json(self) -> dict[str, Any]:
        sumolib = sumo_env.sumolib()
        net = sumolib.net.readNet(self.net)
        lanes = []
        for edge in net.getEdges():
            for lane in edge.getLanes():
                shape = [{"x": round(x, 2), "y": round(y, 2)} for x, y in lane.getShape()]
                lanes.append({
                    "id": lane.getID(),
                    "edge": edge.getID(),
                    "width": round(lane.getWidth(), 2),
                    "speed": round(lane.getSpeed(), 2),
                    "shape": shape,
                })
        tls = []
        for t in net.getTrafficLights():
            x, y = t.getPosition() if hasattr(t, "getPosition") else (0, 0)
            tls.append({"id": t.getID(), "x": round(x, 1), "y": round(y, 1)})
        junctions = [{"id": j.getID(), "x": round(j.getCoord()[0], 1), "y": round(j.getCoord()[1], 1)}
                     for j in net.getNodes()]
        xmin, ymin, xmax, ymax = net.getBoundary()
        return {
            "bounds": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
            "lanes": lanes,
            "junctions": junctions,
            "traffic_lights": tls,
        }

    # ------------------------------------------------------------------
    def tail_log(self, limit: int = 800) -> str:
        path = self._log_path()
        if not path or not path.exists():
            return ""
        try:
            data = path.read_bytes()[-limit:]
            return data.decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.traci is not None:
                self.traci.close()
        except Exception:  # noqa: BLE001
            pass
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._log_fh = None
        self.traci = None
