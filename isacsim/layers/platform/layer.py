"""PlatformLayer：API 网关/IAM/订阅/Ingest/云控/审计的语义平台。"""
from __future__ import annotations

from collections import deque

from ...config import SceneCfg
from ..defense import emit_defense
from ..health import compute_health
from .audit import AuditLog
from .dispatch import Dispatcher
from .iam import IAM
from .ingest import Ingest
from .subscription import SubscriptionManager


class PlatformLayer:
    def __init__(self, scene: SceneCfg) -> None:
        self.scene = scene
        self.cfg = scene.platform
        self.iam = IAM()
        self.audit = AuditLog(enabled=self.cfg.audit)
        self.subs = SubscriptionManager()
        self.ingest = Ingest()
        self.dispatcher = Dispatcher()
        self.service_token_id: str | None = None
        self.tenant_token_id: str | None = None
        self.tenant = self.cfg.tenants[0]
        self._bootstrapped = False
        self._last_t = 0.0
        self._t = 0.0
        # 平台健康度同样看“最近 2 s 是否发生异常”，累计计数只做统计
        self._anom_names = ("ingest_drop", "provenance", "slow", "iam_reject",
                            "disp_reject", "burst", "audit_suppress", "subs_revoke")
        self._anom_prev: dict[str, int] = {}
        self._anom_win: dict[str, deque] = {
            k: deque(maxlen=64) for k in self._anom_names}
        self._closed = False

    # ------------------------------------------------------------------
    def _bootstrap(self, ctx) -> None:
        self.service_token_id = self.iam.issue(
            ctx, subject="rsu:SVC", tenant=self.tenant,
            scopes=["ingest:write"], ttl_s=self.cfg.token_ttl_s,
        ).token_id
        self.tenant_token_id = self.iam.issue(
            ctx, subject=f"tenant:{self.tenant}", tenant=self.tenant,
            scopes=["cpm:read", "denm:read", "spat:read", "control:write"],
            ttl_s=self.cfg.token_ttl_s,
        ).token_id
        self.subs.create(
            ctx, tenant=self.tenant, event_types=["cpm", "denm", "spat"],
            callback="https://platform.local/events",
        )
        self.audit.append(ctx, actor="system", action="bootstrap",
                          resource="platform", result="accept")
        self._bootstrapped = True

    def tick(self, ctx) -> None:
        if not self.cfg.enabled or self._closed:
            return
        if not self._bootstrapped:
            self._bootstrap(ctx)
        events = ctx.extras.pop("v2x_outbox", [])
        # 来源不再写死：N6 路由被改向后，平台收到的真实消息也会带异常 provenance
        # （A7 的“流量劫持”由此变成对真实数据的可观测后果，而不是一条伪造记录）。
        core = ctx.extras.get("layers", {}).get("core")
        upf = getattr(core, "upf", None)
        provenance = getattr(upf, "route_override", None) or "dn:DN-01"
        for event in events:
            msg_type = Ingest._msg_type(event)
            active = self.subs.active_for(msg_type)
            self.ingest.ingest(
                ctx, event,
                token_id=self.service_token_id or "svc-rsu",
                tenant=self.tenant,
                provenance=provenance,
                dropped=not bool(active),
                cause=None if active else "no_subscription",
                allowed_dns=set(getattr(self.scene.core, "allowed_dns", ["dn:DN-01"])),
            )
        self._t = float(ctx.t)
        self._sample_anomalies(self._t)
        self._last_t = ctx.t
        ctx.extras["platform"] = self

    # ------------------------------------------------------------------
    def submit_control(self, ctx, *, token_id: str, vehicle_ids: list[str],
                       command: str = "brake", params: dict | None = None,
                       ttl_s: float = 5.0, span=None):
        return self.dispatcher.submit(
            ctx, iam=self.iam, audit=self.audit, token_id=token_id,
            vehicle_ids=vehicle_ids, command=command, params=params,
            ttl_s=ttl_s, span=span,
        )

    def subscribe(self, ctx, *, tenant: str, event_types: list[str],
                  callback: str, span=None):
        return self.subs.create(ctx, tenant=tenant, event_types=event_types,
                                callback=callback, span=span)

    def revoke_token(self, ctx, token_id: str, *, span=None) -> bool:
        ok = self.iam.revoke(ctx, token_id, span=span)
        self.audit.append(ctx, actor="admin", action="token:revoke",
                          resource=token_id, result="accept" if ok else "reject",
                          span=span)
        emit_defense(ctx, layer="app", action="platform_revoke_token",
                     target=f"token:{token_id}", result="applied" if ok else "failed",
                     span=span)
        return ok

    def quarantine_source(self, ctx, source: str, *, span=None) -> dict:
        self.audit.append(ctx, actor="admin", action="source:quarantine",
                          resource=source, result="accept", span=span)
        return emit_defense(ctx, layer="app", action="platform_quarantine_source",
                            target=source, params={"source": source}, span=span)

    def _sample_anomalies(self, now: float) -> None:
        values = {
            "ingest_drop": self.ingest.dropped,
            "provenance": self.ingest.provenance_bad,
            "slow": self.ingest.slow,
            "iam_reject": self.iam.rejected,
            "disp_reject": self.dispatcher.rejected,
            "burst": self.dispatcher.bursts,
            "audit_suppress": self.audit.suppressed,
            "subs_revoke": self.subs.revoked,
        }
        for name, val in values.items():
            prev = self._anom_prev.get(name)
            self._anom_prev[name] = val
            win = self._anom_win[name]
            if prev is not None and val > prev:
                for _ in range(min(20, val - prev)):
                    win.append(now)
            while win and win[0] < now - 2.0:
                win.popleft()

    def _recent_anomalies(self) -> dict[str, int]:
        return {k: len(q) for k, q in self._anom_win.items() if q}

    def snapshot(self) -> dict:
        elapsed = max(self._last_t, 1.0)
        ingest = self.ingest.snapshot()
        iam = self.iam.snapshot()
        subs = self.subs.snapshot()
        disp = self.dispatcher.snapshot()
        audit = self.audit.snapshot()
        qps = self.ingest.count / elapsed
        recent = self._recent_anomalies()
        anomaly = bool(recent)
        warning = "slow" in recent
        return {
            "enabled": self.cfg.enabled,
            "tenant": self.tenant,
            "qps": round(qps, 2),
            "ingest": ingest,
            "iam": iam,
            "subscriptions": subs,
            "dispatcher": disp,
            "audit": audit,
            "health": compute_health(
                enabled=self.cfg.enabled,
                load=qps / max(1.0, self.cfg.api_qps),
                anomaly=anomaly, warning=warning,
                reasons=[f"recent {k} {v}" for k, v in recent.items()],
                metrics={"qps": round(qps, 2),
                         "api_qps": self.cfg.api_qps,
                         "dropped": ingest["dropped"],
                         "slow": ingest["slow"]},
            ),
        }

    def graph_nodes(self) -> list[dict]:
        return [
            {"id": "platform:V2X-01", "layer": "app", "kind": "platform"},
            {"id": f"tenant:{self.tenant}", "layer": "app", "kind": "tenant"},
        ]

    def graph_edges(self) -> list[dict]:
        return [
            {"src": "dn:DN-01", "dst": "platform:V2X-01", "kind": "api", "latency_ms": 5.0},
            {"src": "platform:V2X-01", "dst": f"tenant:{self.tenant}", "kind": "service",
             "latency_ms": 2.0},
        ]

    def close(self) -> None:
        self._closed = True
