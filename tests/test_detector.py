from isacsim.config import default_scene
from isacsim.core.records import Record
from isacsim.detect.reference import ReferenceDetector
from isacsim import T0_NS


def obj(oid, x, y, cls="obstacle"):
    return {"object_id": oid, "cls": cls, "x": x, "y": y, "z": 1.0,
            "l": 0.6, "w": 0.6, "h": 1.8, "vx": 0.0, "vy": 0.0,
            "heading_deg": 180.0, "confidence": 0.9, "tracked_age": 50,
            "occluded": False, "entity": f"ghost:{oid}"}


def feed(det, t, sensor, objects):
    det.on_record(Record(stream="sensing.objects", ts_ns=T0_NS + int(t * 1e9),
                         source=sensor, entity=f"rsu:{sensor}",
                         payload={"sensor_id": sensor, "coverage_id": f"{sensor}-COV",
                                  "frame_seq": int(t * 10), "objects": objects}))


def test_ghost_detected_after_two_frames():
    scene = default_scene()
    det = ReferenceDetector(scene)
    feed(det, 1.0, "RSU-01", [obj(9001, 120.0, -3.0)])
    feed(det, 1.1, "RSU-01", [obj(9001, 120.0, -3.0)])
    assert any(a.rule == "ghost_object" for a in det.alerts)


def test_map_object_not_flagged():
    scene = default_scene()
    det = ReferenceDetector(scene)
    real = scene.static_objects[0]
    for i in range(4):
        feed(det, 1.0 + i * 0.1, "RSU-01",
             [obj(real["object_id"], real["x"], real["y"], cls=real["cls"])])
    assert not any(a.rule == "ghost_object" for a in det.alerts)


def test_confirmed_object_not_flagged():
    scene = default_scene()
    det = ReferenceDetector(scene)
    for i in range(3):
        t = 1.0 + i * 0.1
        feed(det, t, "RSU-01", [obj(9100, 150.0, -4.0)])
        feed(det, t + 0.02, "RSU-02", [obj(12, 150.0, -4.0)])
    assert not any(a.rule == "ghost_object" for a in det.alerts)


def test_detector_does_not_read_truth():
    """检测器只允许读观测流；静态检查它没有导入/访问 truth。"""
    import ast
    from pathlib import Path

    src = Path("isacsim/detect/reference.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "truth" not in (node.module or ""), "检测器不得导入 truth"
        elif isinstance(node, ast.Import):
            assert all("truth" not in a.name for a in node.names)
    assert "ctx.truth" not in src
    assert ".truth" not in src
