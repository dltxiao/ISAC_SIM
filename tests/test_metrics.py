from isacsim.eval.metrics import cluster_alerts, match_events, wilson


def test_wilson():
    lo, hi = wilson(300, 300)
    assert 0.98 < lo <= 1.0 and hi > 0.999
    lo2, hi2 = wilson(0, 300)
    assert lo2 == 0.0 and hi2 < 0.02


def test_cluster():
    alerts = [{"t": 1.0, "rule": "a", "entity": "e"},
              {"t": 2.0, "rule": "b", "entity": "e"},
              {"t": 10.0, "rule": "a", "entity": "e"}]
    ev = cluster_alerts(alerts, gap=2.5)
    assert len(ev) == 2
    assert ev[0].t0 == 1.0 and ev[0].t1 == 2.0
    assert ev[0].rules == {"a", "b"}


def test_match():
    truth = [{"t0": 1.0, "t1": 3.0}, {"t0": 10.0, "t1": 12.0}]
    det = cluster_alerts([{"t": 2.0, "rule": "x", "entity": "e"},
                          {"t": 11.0, "rule": "y", "entity": "e"}])
    hits, idx, unmatched = match_events(truth, det)
    assert hits == 2 and idx == [0, 1] and unmatched == []


def test_evaluate_runs_splits_envelope(tmp_path):
    """包络外事件单列、不进识别率分母。"""
    import json
    from isacsim.eval.metrics import evaluate_runs

    def write_run(name, events, alerts):
        run = tmp_path / name
        (run / "truth").mkdir(parents=True)
        (run / "truth" / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
        (run / "alerts.jsonl").write_text(
            "\n".join(json.dumps(a, ensure_ascii=False) for a in alerts) + "\n", encoding="utf-8")
        (run / "meta.json").write_text(json.dumps({"kind": "attack"}), encoding="utf-8")
        return run

    base = {"campaign": "A1", "official_type": "x", "label": 1, "nodes": ["veh:a"],
            "domains": ["perception"], "amplitude": 0.8, "params": {}, "stages": [],
            "causal_chain": [], "expected_evidence": [], "seed": 1}
    ev_in = dict(base, event_id="E1", t0=1.0, t1=3.0,
                 envelope={"in_envelope": True, "criterion": "x"})
    ev_out = dict(base, event_id="E2", t0=10.0, t1=12.0,
                  envelope={"in_envelope": False, "criterion": "x"})

    run = write_run("run1", [ev_in, ev_out],
                    [{"t": 2.0, "rule": "ghost_object", "entity": "rsu:R", "latency_ms": 0.1},
                     {"t": 11.0, "rule": "ghost_object", "entity": "rsu:R", "latency_ms": 0.1}])
    result = evaluate_runs([run])
    assert result["attack_events"] == 1, "包络外事件不能计入分母"
    assert result["hits"] == 1
    assert result["out_of_envelope"]["events"] == 1
    assert result["out_of_envelope"]["hits"] == 1
