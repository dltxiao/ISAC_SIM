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
