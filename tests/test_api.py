import pytest
from fastapi.testclient import TestClient

from isacsim.web.api import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_static_and_state(client):
    assert client.get("/").status_code == 200
    r = client.get("/api/state")
    assert r.status_code == 200 and "vehicles" in r.json()
    assert client.get("/api/scenes").status_code == 200
    hw = client.get("/api/hardware").json()
    assert hw["cpu_count"] >= 1


def test_attack_without_session_conflict(client):
    r = client.post("/api/attacks", json={"type": "A1"})
    assert r.status_code == 409


def test_unknown_attack_type(client):
    r = client.post("/api/attacks", json={"type": "NOPE"})
    assert r.status_code in (400, 409)


def test_net_endpoint(client):
    assert client.get("/api/net").status_code == 200


def test_trace_page_and_capabilities(client):
    assert client.get("/trace").status_code == 200
    cap = client.get("/api/trace/capabilities").json()
    assert cap["schema"] == "isacsim.trace/1"
    assert "step_checkpoint" in cap["modes"]
    assert set(cap["profiles"]) == {f"A{i}" for i in range(1, 11)} | {"default"}


def test_trace_page_has_lifecycle_and_a1_to_a10_injection(client):
    html = client.get("/trace").text
    for element_id in ("btnStart", "btnStop", "btnInject", "attackType", "cardTabs"):
        assert f'id="{element_id}"' in html
    for atype in (f"A{i}" for i in range(1, 11)):
        assert f'value="{atype}"' in html
    assert "btnInjectA1" not in html
    assert "btnInjectA5" not in html


def test_main_page_has_no_reset_and_reset_api_removed(client):
    html = client.get("/").text
    assert 'id="btnReset"' not in html
    assert client.post("/api/session/reset").status_code == 404


def test_trace_state_without_session(client):
    state = client.get("/api/trace/state").json()
    assert state["schema"] == "isacsim.trace/1"
    assert state["targets"] == []


def test_trace_step_without_session_conflict(client):
    r = client.post("/api/trace/step", json={"mode": "checkpoint"})
    assert r.status_code == 409


def test_core_platform_chain_defense_endpoints(client):
    assert client.get("/api/core").status_code == 200
    assert client.get("/api/platform").status_code == 200
    chain = client.get("/api/chain").json()
    assert "chains" in chain and "chain_count" in chain
    defense = client.get("/api/defense").json()
    assert "actions" in defense
