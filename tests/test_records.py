import gzip
import json

from isacsim.core.records import JsonlSink, Record


def test_record_schema(tmp_path):
    rec = Record(stream="v2x.cam", ts_ns=123, source="OBU-1", entity="veh:OBU-1",
                 payload={"a": 1})
    d = rec.to_dict("run1")
    assert d["schema"] == "isacsim.record/1"
    assert d["run_id"] == "run1"
    assert d["payload"] == {"a": 1}


def test_jsonl_sink(tmp_path):
    sink = JsonlSink(tmp_path, "run1")
    for i in range(3):
        sink.emit(Record(stream="v2x.cam", ts_ns=i, source="x", entity="e", payload={"i": i}))
    sink.close()
    lines = (tmp_path / "v2x.cam.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["payload"]["i"] == 0


def test_gzip_sink(tmp_path):
    sink = JsonlSink(tmp_path, "run1", compress=True)
    sink.emit(Record(stream="s", ts_ns=1, source="x", entity="e", payload={}))
    sink.close()
    with gzip.open(tmp_path / "s.jsonl.gz", "rt") as fh:
        assert len(fh.read().splitlines()) == 1
