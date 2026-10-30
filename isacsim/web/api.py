"""FastAPI 应用：REST 控制 + SSE 状态推送 + 静态前端。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .session import MANAGER, SessionError

STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="ISAC-SIM 控制台", version=__version__)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/trace")
    def trace_page() -> FileResponse:
        return FileResponse(STATIC / "trace.html")

    @app.get("/api/hardware")
    def hardware() -> dict[str, Any]:
        return MANAGER.hardware()

    @app.get("/api/scenes")
    def scenes() -> list[dict[str, Any]]:
        return [{"id": "arterial", "name": "城市干道（400 m，3 RSU + 1 gNB）"}]

    @app.get("/api/trace/capabilities")
    def trace_capabilities() -> dict[str, Any]:
        state = MANAGER.trace_state()
        return {
            "schema": "isacsim.trace/1",
            "enabled": bool(state.get("enabled")),
            "modes": ["live", "step_tick", "step_checkpoint"],
            "can_control": bool(state.get("enabled")),
            "session": {"state": state.get("state"),
                        "t": state.get("t"), "tick": state.get("tick"),
                        "cursor": state.get("cursor")},
            "profiles": ["A1", "default"],
        }

    @app.get("/api/trace/state")
    def trace_state() -> dict[str, Any]:
        return MANAGER.trace_state()

    @app.get("/api/trace/targets")
    def trace_targets() -> list[dict[str, Any]]:
        return MANAGER.trace_targets()

    @app.get("/api/trace/events")
    def trace_events(after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return MANAGER.trace_events(after, min(1000, max(1, limit)))

    @app.post("/api/trace/step")
    def trace_step(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        try:
            mode = str(body.get("mode", "checkpoint"))
            if mode not in ("checkpoint", "tick"):
                raise HTTPException(400, "mode 必须是 checkpoint 或 tick")
            count = int(body.get("count", 1))
            snap = MANAGER.trace_step(mode=mode, count=count)
            return {"snapshot": snap, "trace": MANAGER.trace_state()}
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/trace/stream")
    async def trace_stream() -> StreamingResponse:
        async def gen():
            last_sig = None
            last_ping = 0.0
            while True:
                try:
                    payload = MANAGER.trace_state()
                    step = payload.get("last_step") or {}
                    sig = (payload.get("cursor", 0), payload.get("state"),
                           step.get("step_id"), payload.get("pause_reason"))
                    now = time.monotonic()
                    if sig != last_sig:
                        last_sig = sig
                        last_ping = now
                        data = json.dumps(payload, ensure_ascii=False)
                        yield f"id: {payload.get('cursor', 0)}\nevent: trace\ndata: {data}\n\n"
                    elif now - last_ping >= 5.0:
                        # 暂停/无变化时只发心跳，避免前端反复重建列表
                        last_ping = now
                        yield ": ping\n\n"
                except Exception as exc:  # noqa: BLE001
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return MANAGER.snapshot()

    @app.get("/api/net")
    def net() -> dict[str, Any]:
        return MANAGER.net() or {}

    @app.get("/api/core")
    def core() -> dict[str, Any]:
        return MANAGER.snapshot().get("core") or {}

    @app.get("/api/platform")
    def platform() -> dict[str, Any]:
        return MANAGER.snapshot().get("platform") or {}

    @app.get("/api/chain")
    def chain() -> dict[str, Any]:
        det = MANAGER.snapshot().get("detector") or {}
        return {"chain_count": det.get("chain_count", 0),
                "chains": det.get("chains", [])}

    @app.get("/api/defense")
    def defense() -> dict[str, Any]:
        return {"actions": MANAGER.snapshot().get("defense_actions", [])}

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return MANAGER.runs()

    @app.get("/api/runs/{run_id}/download")
    def download(run_id: str) -> FileResponse:
        try:
            path = MANAGER.download(run_id)
        except SessionError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, filename=path.name)

    @app.post("/api/session/start")
    def start(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        try:
            return MANAGER.start(
                seed=int(body.get("seed", 11)),
                speed=float(body.get("speed", 1.0)),
                duration=float(body.get("duration", 60.0)),
                scene=str(body.get("scene", "arterial")),
            )
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/session/pause")
    def pause() -> dict[str, Any]:
        return _guard(MANAGER.pause)

    @app.post("/api/session/resume")
    def resume() -> dict[str, Any]:
        return _guard(MANAGER.resume)

    @app.post("/api/session/stop")
    def stop() -> dict[str, Any]:
        return _guard(MANAGER.stop)

    @app.post("/api/session/reset")
    def reset() -> dict[str, Any]:
        return _guard(MANAGER.reset)

    @app.post("/api/session/speed")
    def speed(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        return _guard(lambda: MANAGER.set_speed(float(body.get("speed", 1.0))))

    @app.post("/api/attacks")
    def attack(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        try:
            return MANAGER.submit_attack(body)
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/attacks/{attack_id}")
    def cancel(attack_id: str) -> dict[str, Any]:
        return {"cancelled": MANAGER.cancel_attack(attack_id)}

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        async def gen():
            while True:
                try:
                    payload = json.dumps(MANAGER.snapshot(), ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                except Exception as exc:  # noqa: BLE001
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                await asyncio.sleep(0.15)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return app


def _guard(fn):
    try:
        return fn()
    except SessionError as exc:
        raise HTTPException(409, str(exc)) from exc


def serve(host: str = "127.0.0.1", port: int = 8080, scene_name: str = "arterial",
          open_browser: bool = False) -> None:
    import uvicorn

    url = f"http://{host}:{port}"
    print(f"[isacsim] Web 控制台：{url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
