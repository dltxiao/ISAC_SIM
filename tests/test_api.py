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
