"""5G 核心网 / V2X 平台 provider：把只读 snapshot 投影成 watch target。"""
from __future__ import annotations

from typing import Any

from .base import BaseProvider, ProviderFrame, ProviderTarget


CORE_UE_FIELDS = [
    {"name": "ue_id", "type": "string", "role": "identity"},
    {"name": "gnb_id", "type": "string"},
    {"name": "state", "type": "enum"},
    {"name": "pdu_session_id", "type": "string"},
]

CORE_SESSION_FIELDS = [
    {"name": "session_id", "type": "string", "role": "identity"},
    {"name": "ue_id", "type": "string"},
    {"name": "dnn", "type": "string"},
    {"name": "snssai", "type": "string"},
    {"name": "upf_id", "type": "string"},
    {"name": "ue_ip", "type": "string"},
    {"name": "qfi", "type": "int"},
    {"name": "five_qi", "type": "int"},
    {"name": "gfbr_kbps", "type": "int"},
    {"name": "state", "type": "enum"},
]

CORE_FLOW_FIELDS = [
    {"name": "flow_id", "type": "string", "role": "identity"},
    {"name": "ue_id", "type": "string"},
    {"name": "session_id", "type": "string"},
    {"name": "qfi", "type": "int"},
    {"name": "five_qi", "type": "int"},
    {"name": "teid", "type": "int"},
    {"name": "route", "type": "string"},
    {"name": "state", "type": "enum"},
]

PLATFORM_TOKEN_FIELDS = [
    {"name": "token_id", "type": "string", "role": "identity"},
    {"name": "subject", "type": "string"},
    {"name": "tenant", "type": "string"},
    {"name": "scopes", "type": "string"},
    {"name": "issued_at", "type": "float", "unit": "s", "precision": 2},
    {"name": "expires_at", "type": "float", "unit": "s", "precision": 2},
    {"name": "revoked", "type": "bool", "role": "flag"},
]

PLATFORM_SUBSCRIPTION_FIELDS = [
    {"name": "sub_id", "type": "string", "role": "identity"},
    {"name": "tenant", "type": "string"},
    {"name": "event_types", "type": "string"},
    {"name": "callback", "type": "string"},
    {"name": "state", "type": "enum"},
]

CORE_NODES = [
    ("core:amf", "amf", "AMF", 33),
    ("core:smf", "smf", "SMF", 34),
    ("core:upf", "upf", "UPF", 35),
    ("core:nef", "nef", "NEF", 36),
    ("core:udm", "udm", "UDM", 37),
    ("core:ausf", "ausf", "AUSF", 38),
    ("core:dn", "dn", "DN", 39),
    ("core:gnb", "gnb", "gNB", 40),
]


def _health(node: dict[str, Any]) -> dict[str, Any]:
    h = node.get("health") or {}
    return {
        "health_status": h.get("status", "unknown"),
        "health_score": h.get("score"),
        "health_reasons": ", ".join(str(x) for x in (h.get("reasons") or [])),
    }


def _node_values(node: dict[str, Any]) -> dict[str, Any]:
    """节点：标量计数 + health；跳过嵌套的 ues/health。"""
    values: dict[str, Any] = {"entity": node.get("entity")}
    for key, value in node.items():
        if key in ("health", "ues") or isinstance(value, (dict, list)):
            continue
        values[key] = value
    values.update(_health(node))
    return values


class CoreProvider(BaseProvider):
    id = "core"
    interval_s = 0.1

    def available(self) -> bool:
        return getattr(self.sim, "core", None) is not None

    def targets(self) -> list[ProviderTarget]:
        if not self.available():
            return []
        out = [
            ProviderTarget(
                "core:summary", "struct", "Core", "Core · summary",
                "核心网附着/注册/会话/流表总览；来源 sim.core.snapshot()",
                order=32,
            )
        ]
        out.extend(
            ProviderTarget(
                tid, "struct", "Core", f"{label} · node",
                f"{label} 计数、路由与健康状态",
                order=order,
            )
            for tid, _key, label, order in CORE_NODES
        )
        out.append(
            ProviderTarget(
                "core:ues", "table", "Core", "UE · contexts",
                "已注册 UE 的 gNB/会话状态（最多 40 条）",
                fields=CORE_UE_FIELDS, key_field="ue_id", order=41,
            )
        )
        out.append(
            ProviderTarget(
                "core:sessions", "table", "Core", "PDU · sessions",
                "PDU 会话与 QoS（最多 40 条）；来源 SMF snapshot.session_list",
                fields=CORE_SESSION_FIELDS, key_field="session_id", order=42,
            )
        )
        out.append(
            ProviderTarget(
                "core:flows", "table", "Core", "UPF · flows",
                "UPF 流表/TEID/路由（最多 40 条）；来源 UPF snapshot.flow_list",
                fields=CORE_FLOW_FIELDS, key_field="flow_id", order=43,
            )
        )
        return out

    def poll(self, ctx) -> list[ProviderFrame]:
        if not self.available() or not self.due():
            return []
        self.mark()
        try:
            snap = self.sim.core.snapshot()
        except Exception:  # noqa: BLE001 - trace 不得中断仿真
            return []

        nodes = {key: snap.get(key) or {} for _tid, key, _label, _order in CORE_NODES}
        bad = sum(
            1 for node in nodes.values()
            if (node.get("health") or {}).get("status") in ("orange", "red")
        )
        summary = {
            "enabled": snap.get("enabled", True),
            "attached": snap.get("attached", 0),
            "registered": snap.get("registered", 0),
            "sessions": snap.get("sessions", 0),
            "flows": snap.get("flows", 0),
            "bus_pending": snap.get("bus_pending", 0),
            "health_bad": bad,
        }
        frames = [
            ProviderFrame(
                "core:summary", summary,
                summary=f"attached {summary['attached']} · sessions {summary['sessions']}",
            )
        ]
        for tid, key, label, _order in CORE_NODES:
            node = nodes[key]
            values = _node_values(node)
            status = (node.get("health") or {}).get("status", "unknown")
            frames.append(ProviderFrame(
                tid, values,
                summary=f"{label} · {status}",
            ))

        ues = (snap.get("amf") or {}).get("ues") or []
        rows = [
            {
                "ue_id": u.get("ue_id"),
                "gnb_id": u.get("gnb_id"),
                "state": u.get("state"),
                "pdu_session_id": u.get("pdu_session_id"),
            }
            for u in ues[:40]
        ]
        frames.append(ProviderFrame(
            "core:ues", {"rows": rows, "total": len(ues), "truncated": len(ues) > 40},
            summary=f"{len(ues)} UE",
        ))

        smf = snap.get("smf") or {}
        sessions = smf.get("session_list") or []
        frames.append(ProviderFrame(
            "core:sessions",
            {
                "rows": sessions[:40],
                "total": smf.get("session_list_total", len(sessions)),
                "truncated": bool(smf.get("session_list_truncated", len(sessions) > 40)),
            },
            summary=f"{smf.get('session_list_total', len(sessions))} sessions",
        ))

        upf = snap.get("upf") or {}
        flows = upf.get("flow_list") or []
        frames.append(ProviderFrame(
            "core:flows",
            {
                "rows": flows[:40],
                "total": upf.get("flow_list_total", len(flows)),
                "truncated": bool(upf.get("flow_list_truncated", len(flows) > 40)),
            },
            summary=f"{upf.get('flow_list_total', len(flows))} flows",
        ))
        return frames


class PlatformProvider(BaseProvider):
    id = "platform"
    interval_s = 0.1

    def available(self) -> bool:
        return getattr(self.sim, "platform", None) is not None

    def targets(self) -> list[ProviderTarget]:
        if not self.available():
            return []
        return [
            ProviderTarget(
                "platform:summary", "struct", "Platform", "V2X platform · summary",
                "平台 IAM/订阅/Ingest/云控/审计与健康状态",
                order=44,
            ),
            ProviderTarget(
                "platform:tokens", "table", "Platform", "Platform · tokens",
                "IAM token（最多 40 条）；来源 platform.iam.token_list",
                fields=PLATFORM_TOKEN_FIELDS, key_field="token_id", order=45,
            ),
            ProviderTarget(
                "platform:subscriptions", "table", "Platform", "Platform · subscriptions",
                "租户订阅（最多 40 条）；来源 platform.subs.subscription_list",
                fields=PLATFORM_SUBSCRIPTION_FIELDS, key_field="sub_id", order=46,
            ),
        ]

    def poll(self, ctx) -> list[ProviderFrame]:
        if not self.available() or not self.due():
            return []
        self.mark()
        try:
            snap = self.sim.platform.snapshot()
        except Exception:  # noqa: BLE001
            return []

        ingest = snap.get("ingest") or {}
        iam = snap.get("iam") or {}
        subs = snap.get("subscriptions") or {}
        disp = snap.get("dispatcher") or {}
        audit = snap.get("audit") or {}
        h = snap.get("health") or {}
        values = {
            "enabled": snap.get("enabled", True),
            "tenant": snap.get("tenant"),
            "qps": snap.get("qps", 0.0),
            "ingested": ingest.get("ingested", ingest.get("total", 0)),
            "ingest_dropped": ingest.get("dropped", 0),
            "ingest_slow": ingest.get("slow", 0),
            "tokens": iam.get("tokens", 0),
            "iam_rejected": iam.get("rejected", 0),
            "subscriptions": subs.get("active", 0),
            "control_sent": disp.get("sent", 0),
            "control_rejected": disp.get("rejected", 0),
            "audit_seq": audit.get("audit_seq", 0),
            "audit_suppressed": audit.get("suppressed", 0),
            "health_status": h.get("status", "unknown"),
            "health_score": h.get("score"),
            "health_reasons": ", ".join(str(x) for x in (h.get("reasons") or [])),
        }
        frames = [
            ProviderFrame(
                "platform:summary", values,
                summary=f"{values['health_status']} · qps {values['qps']}",
            )
        ]
        tokens = iam.get("token_list") or []
        frames.append(ProviderFrame(
            "platform:tokens",
            {
                "rows": tokens[:40],
                "total": iam.get("token_list_total", len(tokens)),
                "truncated": bool(iam.get("token_list_truncated", len(tokens) > 40)),
            },
            summary=f"{iam.get('token_list_total', len(tokens))} tokens",
        ))
        subscriptions = subs.get("subscription_list") or []
        frames.append(ProviderFrame(
            "platform:subscriptions",
            {
                "rows": subscriptions[:40],
                "total": subs.get("subscription_list_total", len(subscriptions)),
                "truncated": bool(subs.get("subscription_list_truncated",
                                           len(subscriptions) > 40)),
            },
            summary=f"{subs.get('subscription_list_total', len(subscriptions))} subscriptions",
        ))
        return frames
