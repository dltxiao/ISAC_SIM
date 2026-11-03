import os
import time

import pytest
from fastapi.testclient import TestClient

from isacsim.web.api import create_app
from isacsim.web.storage import StorageService, normalize_policy


def _run(root, name: str, days_ago: float, *, meta: bool = False) -> None:
    path = root / "web" / name
    (path / "truth").mkdir(parents=True)
    (path / "truth" / "events.jsonl").write_text("", encoding="utf-8")
    (path / "sensing.gnss.jsonl").write_text("x" * 2048, encoding="utf-8")
    if meta:
        (path / "meta.json").write_text("{}", encoding="utf-8")
    ts = time.time() - days_ago * 86400
    os.utime(path, (ts, ts))


def _batch(root, name: str, runs: int = 2) -> None:
    for i in range(runs):
        child = root / name / f"train-{i:03d}-A1"
        child.mkdir(parents=True)
        (child / "meta.json").write_text("{}", encoding="utf-8")
        (child / "sensing.gnss.jsonl").write_text("y" * 1024, encoding="utf-8")


@pytest.fixture()
def root(tmp_path):
    return tmp_path


def test_policy_validation():
    assert normalize_policy({"scope": ["web"], "keep": 2})["keep"] == 2
    with pytest.raises(ValueError):
        normalize_policy({"scope": []})
    with pytest.raises(ValueError):
        normalize_policy({"scope": ["web"], "keep": -1})
    with pytest.raises(ValueError):
        normalize_policy({"scope": ["web"], "older_than_days": -2})


def test_summary_and_plan_scope(root):
    for i, days in enumerate((1, 2, 3), start=1):
        _run(root, f"web-{i}", days)
    _batch(root, "ds", 2)

    svc = StorageService(root)
    summary = svc.summary()
    assert summary["web"]["count"] == 3
    assert summary["batch"]["count"] == 1
    assert summary["out_bytes"] > 0

    plan = svc.plan({"scope": ["web"], "keep": 1})
    assert plan["delete_count"] == 2
    assert {i["name"] for i in plan["items"]} == {"web-2", "web-3"}
    assert plan["kept"][0]["name"] == "web-1"

    plan = svc.plan({"scope": ["batch"], "keep": 0})
    assert plan["delete_count"] == 1
    assert plan["items"][0]["kind"] == "batch"
    assert plan["items"][0]["runs"] == 2


def test_active_run_is_protected(root):
    for i, days in enumerate((1, 2), start=1):
        _run(root, f"web-{i}", days)
    svc = StorageService(root)
    protected = root / "web" / "web-1"
    plan = svc.plan({"scope": ["web"], "keep": 0, "all": True}, protect=[protected])
    assert [i["name"] for i in plan["items"]] == ["web-2"]
    assert plan["protected_count"] == 1


def test_cleanup_api_requires_confirm_and_deletes(root):
    _run(root, "web-keep", 1)
    _run(root, "web-drop", 2)
    client = TestClient(create_app(storage_root=root))

    body = {"scope": ["web"], "keep": 1}
    assert client.post("/api/storage/cleanup", json=body).status_code == 400

    preview = client.post("/api/storage/plan", json=body).json()
    assert preview["delete_count"] == 1

    started = client.post("/api/storage/cleanup", json={**body, "confirm": True})
    assert started.status_code == 200 and started.json()["state"] in ("running", "done")

    status = {}
    for _ in range(50):
        status = client.get("/api/storage/cleanup").json()
        if status["state"] == "done":
            break
        time.sleep(0.05)
    assert status["state"] == "done"
    assert status["removed"] == 1 and status["freed_bytes"] > 0
    assert (root / "web" / "web-keep").is_dir()
    assert not (root / "web" / "web-drop").exists()

    assert client.get("/api/storage").json()["web"]["count"] == 1


def test_zip_is_counted_and_removed(root):
    _run(root, "web-zip", 2)
    (root / "web" / "web-zip.zip").write_bytes(b"z" * 4096)
    svc = StorageService(root)
    item = svc.plan({"scope": ["web"], "keep": 0, "all": True})["items"][0]
    assert item["size_bytes"] > 4096
    result = svc.start({"scope": ["web"], "keep": 0, "all": True, "confirm": True})
    svc.wait(10)
    assert not (root / "web" / "web-zip.zip").exists()
    assert result["total"] == 1
