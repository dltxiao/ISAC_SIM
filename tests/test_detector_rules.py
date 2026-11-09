"""检测规则的“合法操作不误报”阈值（R5）。

用合成记录喂 `ReferenceDetector`，覆盖本轮收紧/放宽的五条规则：
QoS 变更、N6 路由、ingest 来源、订阅退订、多车云控。
"""
from isacsim import T0_NS
from isacsim.config import default_scene
from isacsim.core.records import Record
from isacsim.detect.reference import ReferenceDetector


def _rec(t, stream, payload, source="x", entity="e"):
    return Record(stream=stream, ts_ns=T0_NS + int(t * 1e9),
                  source=source, entity=entity, payload=payload)


def _det():
    return ReferenceDetector(default_scene(seed=3))


def _rules(det):
    return {a.rule for a in det.alerts}


def test_qos_downgrade_requires_bandwidth_cut():
    det = _det()
    # 合法割接：79→8、800 kbps
    det.on_record(_rec(1.0, "core.qos", {"msg_type": "qos_flow_modify", "5qi": 8,
                                         "gfbr_kbps": 800}))
    assert "qos_downgrade" not in _rules(det)
    # 真实抢占：79→9、360 kbps
    det.on_record(_rec(2.0, "core.qos", {"msg_type": "qos_flow_modify", "5qi": 9,
                                         "gfbr_kbps": 360}))
    assert "qos_downgrade" in _rules(det)


def test_route_change_uses_allowlist():
    det = _det()
    det.on_record(_rec(1.0, "core.upf", {"msg_type": "route_change", "route": "dn:DN-02"}))
    assert "upf_route_drift" not in _rules(det)
    det.on_record(_rec(2.0, "core.upf", {"msg_type": "route_change", "route": "dn:EVIL-01"}))
    assert "upf_route_drift" in _rules(det)


def test_ingest_provenance_uses_allowlist():
    det = _det()
    det.on_record(_rec(1.0, "app.ingest", {"msg_type": "cpm", "provenance": "dn:DN-02"}))
    assert "ingest_provenance_mismatch" not in _rules(det)
    det.on_record(_rec(2.0, "app.ingest", {"msg_type": "cpm", "provenance": "dn:EVIL-01"}))
    assert "ingest_provenance_mismatch" in _rules(det)


def test_subscription_tamper_needs_multiple_event_types():
    det = _det()
    det.on_record(_rec(1.0, "app.subscription", {
        "action": "revoke", "result": "accept", "subscription_id": "sub-1",
        "event_types": ["spat"]}))
    assert "subscription_tamper" not in _rules(det)
    det.on_record(_rec(2.0, "app.subscription", {
        "action": "revoke", "result": "accept", "subscription_id": "sub-2",
        "event_types": ["denm", "spat", "cpm"]}))
    assert "subscription_tamper" in _rules(det)


def test_cloud_burst_needs_sustained_batches():
    det = _det()
    ids = ["a", "b", "c", "d"]
    # 两次批量（合法车队控制）
    det.on_record(_rec(1.0, "app.control", {"result": "accept", "vehicle_ids": ids,
                                            "command": "brake"}))
    det.on_record(_rec(2.0, "app.control", {"result": "accept", "vehicle_ids": ids,
                                            "command": "brake"}))
    assert "cloud_control_burst" not in _rules(det)
    # 第三次（2 s 内持续高频）
    det.on_record(_rec(2.5, "app.control", {"result": "accept", "vehicle_ids": ids,
                                            "command": "brake"}))
    assert "cloud_control_burst" in _rules(det)
