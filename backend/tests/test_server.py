"""Server API tests: REST endpoints, a full live scripted session over the
WebSocket, pause/speed/stop controls, and replay from storage."""

import time

import pytest
from fastapi.testclient import TestClient

import atc.server as server_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from atc.storage import RunStore
    monkeypatch.setattr(server_mod, "store",
                        RunStore(str(tmp_path / "server.sqlite3")))
    server_mod.runner = server_mod.SessionRunner()
    with TestClient(server_mod.app) as c:
        yield c
    server_mod.runner.stop()


def test_airport_endpoint(client):
    r = client.get("/api/airport/kmbs")
    assert r.status_code == 200
    data = r.json()
    assert {rw["id"] for rw in data["runways"]} == {"05/23", "14/32"}
    assert len(data["nodes"]) > 20
    assert client.get("/api/airport/nowhere").status_code == 404


def test_scenarios_endpoint(client):
    ids = {s["id"] for s in client.get("/api/scenarios").json()}
    assert {"baseline", "rush_hour", "runway_change", "degraded",
            "emergency", "scripted_demo"} <= ids


def test_live_scripted_session_end_to_end(client):
    r = client.post("/api/session/start", json={
        "scenario": "scripted_demo", "controller": "scripted", "speed": 60})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "live"

    # A second start while running conflicts.
    r2 = client.post("/api/session/start", json={
        "scenario": "scripted_demo", "controller": "scripted"})
    assert r2.status_code == 409

    got_snapshot = got_events = got_metrics = saved = False
    with client.websocket_connect("/ws") as ws:
        deadline = time.time() + 90
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "snapshot":
                got_snapshot = True
            elif msg["type"] == "events":
                got_events = True
            elif msg["type"] == "metrics":
                got_metrics = True
            elif msg["type"] == "run_saved":
                saved = True
                break
    assert got_snapshot and got_events and got_metrics and saved

    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["completed"] is True
    run_id = runs[0]["id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["metrics"]["completed_takeoffs"] == 2
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert any(e["type"] == "atc_instruction" for e in events)


def test_controls_and_replay(client):
    client.post("/api/session/start", json={
        "scenario": "scripted_demo", "controller": "scripted", "speed": 60})
    # Pause freezes the sim clock.
    client.post("/api/session/control", json={"action": "pause"})
    t1 = client.get("/api/status").json()["t_s"]
    time.sleep(0.3)
    t2 = client.get("/api/status").json()["t_s"]
    assert t2 <= t1 + 1
    client.post("/api/session/control", json={"action": "speed",
                                              "speed": 60})
    client.post("/api/session/control", json={"action": "play"})
    # Run to completion.
    deadline = time.time() + 90
    while time.time() < deadline:
        if server_mod.runner.mode == "idle" and \
                not server_mod.runner.running:
            break
        time.sleep(0.2)
    runs = client.get("/api/runs").json()
    assert runs, "run should be stored after completion"

    # Replay it.
    r = client.post("/api/session/replay", json={
        "run_id": runs[0]["id"], "speed": 60})
    assert r.status_code == 200
    with client.websocket_connect("/ws") as ws:
        deadline = time.time() + 30
        snaps = 0
        while time.time() < deadline and snaps < 5:
            msg = ws.receive_json()
            if msg["type"] == "snapshot":
                snaps += 1
        assert snaps >= 5
    client.post("/api/session/control", json={"action": "stop"})


def test_inject_requires_live_session(client):
    r = client.post("/api/session/inject", json={
        "kind": "runway_closure", "params": {"runway_id": "14/32"}})
    assert r.status_code == 409
