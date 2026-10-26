import json

from isacsim.io.adapters.nidd import export_nidd
from isacsim import T0_NS


def test_export_nidd(tmp_path):
    run = tmp_path / "run"
    (run / "truth").mkdir(parents=True)
    ts = T0_NS + 1_000_000

    def dump(name, rows):
        with (run / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for p in rows:
                fh.write(json.dumps({"schema": "isacsim.record/1", "run_id": "r",
                                     "stream": name, "ts_ns": ts, "t_wall_ns": 0,
                                     "source": "S", "entity": "veh:V",
                                     "payload": p}) + "\n")

    dump("sensing.radar.track", [{"frame_seq": 1, "sensor_id": "RSU-01", "target_id": 1,
                                  "range_m": 10.0, "radial_vel_mps": 1.0, "azimuth_deg": 0.0,
                                  "elevation_deg": 0.0, "rcs_dbsm": 9.0, "snr_db": 20.0,
                                  "confidence": 0.9}])
    dump("v2x.cam", [{"sender_pseudo": "OBU-1", "msg_id": 1, "posx": 1.0, "posy": 2.0}])
    dump("sensing.objects", [{"frame_seq": 1, "sensor_id": "RSU-01", "coverage_id": "C",
                              "objects": []}])
    (run / "truth" / "events.jsonl").write_text(json.dumps({
        "event_id": "EVT-1", "campaign": "A1", "official_type": "x", "label": 1,
        "t0": 0.0, "t1": 1.0, "nodes": [], "domains": [], "amplitude": 0.5,
    }) + "\n", encoding="utf-8")

    out = tmp_path / "nidd"
    counts = export_nidd(run, out)
    assert counts["d0.jsonl"] == 1
    assert counts["cam.pcapng"] == 1
    assert (out / "labels.json").exists()
    assert (out / "cam.pcapng").read_bytes()[:4] == b"\x0a\x0d\x0d\x0a"
    assert (out / "ground-truth.csv").exists()
