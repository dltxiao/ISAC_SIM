"""Ingest：接收 CPM/DENM/SPaT，校验来源/模式，产生 app.ingest。"""
from __future__ import annotations

from typing import Any


class Ingest:
    def __init__(self) -> None:
        self.count = 0
        self.rejected = 0
        self.slow = 0
        self.dropped = 0
        self.max_latency_ms = 0.0
        self.provenance_bad = 0

    @staticmethod
    def _msg_type(event: dict[str, Any]) -> str:
        stream = str(event.get("stream", ""))
        payload = event.get("payload") or {}
        if payload.get("msg_type"):
            return str(payload["msg_type"])
        return stream.rsplit(".", 1)[-1]

    @staticmethod
    def _source(event: dict[str, Any]) -> str:
        payload = event.get("payload") or {}
        return str(payload.get("sender") or payload.get("sender_pseudo")
                   or payload.get("sensor_id") or "unknown")

    @staticmethod
    def _schema_ok(event: dict[str, Any]) -> bool:
        payload = event.get("payload") or {}
        msg_type = Ingest._msg_type(event)
        if msg_type == "cpm":
            return "objects" in payload or "object_count" in payload
        if msg_type == "denm":
            return "posx" in payload and "posy" in payload
        if msg_type == "spat":
            return "signal_state" in payload
        return True

    def ingest(self, ctx, event: dict[str, Any], *, token_id: str,
               tenant: str, provenance: str = "dn:DN-01",
               dropped: bool = False, cause: str | None = None,
               trace=None) -> dict[str, Any] | None:
        msg_type = self._msg_type(event)
        source = self._source(event)
        ok = self._schema_ok(event)
        if not ok:
            self.rejected += 1
        self.count += 1
        latency = 2.0 + float(ctx.extras.get("platform_ingest_delay_ms", 0.0))
        result = "dropped" if dropped else ("accept" if ok else "reject")
        if dropped:
            self.dropped += 1
        if latency >= 50.0:
            self.slow += 1
        self.max_latency_ms = max(self.max_latency_ms, latency)
        if provenance not in (None, "dn:DN-01"):
            self.provenance_bad += 1
        payload = {
            "msg_type": msg_type,
            "platform_id": "platform:V2X-01",
            "source": source,
            "provenance": provenance,
            "token_id": token_id,
            "tenant": tenant,
            "schema_ok": ok,
            "result": result,
            "cause": cause,
            "latency_ms": round(latency, 3),
        }
        ctx.emit("app.ingest", source, "platform:V2X-01", payload, trace=trace)
        return payload

    def snapshot(self) -> dict:
        return {
            "ingested": self.count,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "slow": self.slow,
            "max_latency_ms": round(self.max_latency_ms, 2),
            "provenance_bad": self.provenance_bad,
        }
