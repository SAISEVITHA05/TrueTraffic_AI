import json
import pytest

from backend import app as app_module
from backend.risk_model import calculate_risk, in_time_window, get_risk_level


@pytest.fixture()
def client(tmp_path, monkeypatch):
    ev = tmp_path / "events.json"
    ev.write_text(json.dumps({"events": []}))
    monkeypatch.setattr(app_module, "EVENTS_JSON", ev)
    monkeypatch.setenv("ADMIN_PASSWORD", "test-pass")
    app_module._tokens.clear()
    return app_module.app.test_client()


def login(client):
    r = client.post("/api/admin/login", json={"password": "test-pass"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.get_json()['token']}"}


def test_health(client):
    assert client.get("/api/health").get_json() == {"status": "ok"}


def test_risk_model_bounds_and_levels():
    assert calculate_risk(95, 23, "Rainy", "Saturday", []) == 100
    assert calculate_risk(30, 12, "Clear", "Monday", []) == 30
    assert get_risk_level(80) == "CRITICAL" and get_risk_level(10) == "LOW"


def test_time_window_wraps_midnight():
    assert in_time_window(23, "22:00-02:00") and in_time_window(1, "22:00-02:00")
    assert not in_time_window(12, "22:00-02:00")


def test_routes_depend_on_origin_and_destination(client):
    a = client.post("/api/routes", json={"origin": "Gajuwaka", "destination": "Kommadi"}).get_json()
    b = client.post("/api/routes", json={"origin": "RK Beach", "destination": "Siripuram"}).get_json()
    assert a["routes"]["fastest"]["distance"] != b["routes"]["fastest"]["distance"]
    assert a["ai_explanation"]


def test_routes_validation(client):
    assert client.post("/api/routes", json={"origin": "X", "destination": "Kommadi"}).status_code == 400
    assert client.post("/api/routes", json={"origin": "Kommadi", "destination": "Kommadi"}).status_code == 400
    assert client.post("/api/routes", json={"origin": "Gajuwaka", "destination": "Kommadi", "hour": 99}).status_code == 400


def test_admin_requires_auth(client):
    assert client.post("/api/events", json={}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "nope"}).status_code == 401


def test_full_restriction_blocks_road(client):
    h = login(client)
    r = client.post("/api/events", headers=h, json={
        "road": "Steel Plant Road", "type": "Road Work", "start_time": "00:00",
        "end_time": "23:59", "severity": "Full Restriction"})
    assert r.status_code == 200
    out = client.post("/api/routes", json={"origin": "Gajuwaka", "destination": "Kommadi", "hour": 12}).get_json()
    assert "Steel Plant Road" in out["routes"]["blocked_roads"]
    for k in ("fastest", "safest", "recommended"):
        assert "Steel Plant Road" not in [x["name"] for x in out["routes"][k]["roads"]]
    assert client.delete("/api/events/0", headers=h).status_code == 200
    assert client.get("/api/events").get_json()["events"] == []


def test_stats(client):
    s = client.get("/api/stats").get_json()
    assert s["total_records"] == 50000
