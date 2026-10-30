from isacsim.detect.chain import build_chains
from isacsim.detect.reference import Alert
from isacsim.eval.chain_metrics import aggregate, compare


def test_build_chains_groups_by_time():
    alerts = [
        Alert(t=1.0, rule="nas_registration_flood", entity="amf:AMF-01"),
        Alert(t=1.5, rule="brake_without_hazard", entity="veh:v1"),
        Alert(t=10.0, rule="upf_route_drift", entity="upf:UPF-01"),
    ]
    chains = build_chains(alerts, gap=2.5)
    assert len(chains) == 2
    assert chains[0].root_cause == "amf:AMF-01"
    assert {n.id for n in chains[0].nodes} == {"amf:AMF-01", "veh:v1"}
    assert chains[0].stages


def test_chain_compare_and_aggregate():
    truth = {
        "nodes": ["amf:AMF-01", "veh:v1", "attacker:A5"],
        "stages": [{"name": "a"}, {"name": "b"}],
    }
    chain = {
        "nodes": [{"id": "amf:AMF-01"}, {"id": "veh:v1"}],
        "stages": [{"name": "a"}],
        "root_cause": "amf:AMF-01",
    }
    row = compare(truth, chain)
    assert row["node_recall"] == 2 / 3
    assert row["node_precision"] == 1.0
    assert row["root_hit"] == 1.0
    assert row["stage_ratio"] == 0.5
    agg = aggregate([row])
    assert agg["chains"] == 1
    assert abs(agg["node_recall"] - 0.6667) < 1e-3
