import hmac
import json
import logging
import os
import secrets
import threading
import time
from functools import wraps

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO)

from backend import bigquery_client  # noqa: E402
from backend.ai_agent import get_ai_explanation  # noqa: E402
from backend.paths import ACCIDENTS_CSV, EVENTS_JSON, FRONTEND_DIR, ROADS_JSON  # noqa: E402
from backend.risk_model import active_events, calculate_risk, get_risk_level  # noqa: E402
from backend.route_engine import RouteError, get_routes  # noqa: E402

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app, resources={r"/api/*": {"origins": "*"}})

VALID_WEATHER = {"Clear", "Rainy", "Foggy", "Windy"}
VALID_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
VALID_SEVERITY = {"Full Restriction", "Partial", "Caution"}

with open(ROADS_JSON, encoding="utf-8") as f:
    road_data = json.load(f)
ROAD_NAMES = {r["name"] for r in road_data["roads"]}

# ---- admin auth (token issued after password login) ------------------------
_events_lock = threading.Lock()
_tokens = {}  # token -> expiry
TOKEN_TTL = 8 * 3600


def _admin_password():
    # No default: if ADMIN_PASSWORD isn't set the admin panel is disabled.
    return os.getenv("ADMIN_PASSWORD")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        exp = _tokens.get(token)
        if not exp or exp < time.time():
            _tokens.pop(token, None)
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper


@app.post("/api/admin/login")
def admin_login():
    pwd = _admin_password()
    if not pwd:
        return jsonify({"error": "Admin disabled: set ADMIN_PASSWORD"}), 503
    given = (request.get_json(silent=True) or {}).get("password", "")
    if not hmac.compare_digest(str(given).encode(), pwd.encode()):
        time.sleep(0.5)  # slow down brute force
        return jsonify({"error": "Wrong password"}), 401
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL
    return jsonify({"token": token})


# ---- events storage ---------------------------------------------------------
def load_events():
    try:
        with open(EVENTS_JSON, encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_events(events):
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump({"events": events}, f, indent=2)


# ---- input parsing ----------------------------------------------------------
def parse_conditions(src):
    try:
        hour = int(src.get("hour", 12))
    except (TypeError, ValueError):
        raise ValueError("hour must be an integer 0-23")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    weather = src.get("weather", "Clear")
    day = src.get("day", "Monday")
    if weather not in VALID_WEATHER:
        raise ValueError(f"weather must be one of {sorted(VALID_WEATHER)}")
    if day not in VALID_DAYS:
        raise ValueError("invalid day")
    return hour, weather, day


@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify({"error": str(e)}), 400


# ---- routes -----------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/roads")
def get_roads():
    return jsonify(road_data)


@app.get("/api/risk")
def get_risk_scores():
    hour, weather, day = parse_conditions(request.args)
    ev = active_events(load_events(), hour)
    results = []
    for road in road_data["roads"]:
        e = ev.get(road["name"])
        risk = calculate_risk(road["base_risk"], hour, weather, day, road["peak_hours"],
                              e["severity"] if e else None)
        results.append({
            "id": road["id"], "name": road["name"], "area": road["area"],
            "coordinates": road["coordinates"], "risk_score": risk,
            "risk_level": get_risk_level(risk), "speed_limit": road["speed_limit"],
            "known_issues": road["known_issues"],
            "restricted": bool(e and e["severity"] == "Full Restriction"),
        })
    return jsonify({"roads": results, "hour": hour, "weather": weather})


@app.post("/api/routes")
def find_routes():
    data = request.get_json(silent=True) or {}
    hour, weather, day = parse_conditions(data)
    origin, destination = data.get("origin"), data.get("destination")
    try:
        routes = get_routes(origin, destination, hour, weather, day, road_data, load_events())
    except RouteError as e:
        return jsonify({"error": str(e)}), 400
    text, source = get_ai_explanation(origin, destination, routes, hour, weather)
    return jsonify({"origin": origin, "destination": destination, "routes": routes,
                    "ai_explanation": text, "ai_source": source})


@app.get("/api/events")
def get_events():
    return jsonify({"events": load_events()})


@app.post("/api/events")
@admin_required
def add_event():
    d = request.get_json(silent=True) or {}
    if d.get("road") not in ROAD_NAMES:
        return jsonify({"error": "Unknown road"}), 400
    if d.get("severity") not in VALID_SEVERITY:
        return jsonify({"error": "Invalid severity"}), 400
    for k in ("start_time", "end_time"):
        try:
            h, m = str(d.get(k, "")).split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except Exception:
            return jsonify({"error": f"{k} must be HH:MM"}), 400
    event = {"road": d["road"], "type": str(d.get("type", ""))[:60],
             "start_time": d["start_time"], "end_time": d["end_time"],
             "severity": d["severity"], "description": str(d.get("description", ""))[:300]}
    with _events_lock:
        events = load_events()
        events.append(event)
        save_events(events)
    return jsonify({"success": True, "event": event})


@app.delete("/api/events/<int:index>")
@admin_required
def delete_event(index):
    with _events_lock:
        events = load_events()
        if not 0 <= index < len(events):
            return jsonify({"error": "No such event"}), 404
        removed = events.pop(index)
        save_events(events)
    return jsonify({"success": True, "removed": removed})


@app.post("/api/events/clear")
@admin_required
def clear_events():
    with _events_lock:
        save_events([])
    return jsonify({"success": True})


_stats_cache = {}


def _compute_stats():
    df = pd.read_csv(ACCIDENTS_CSV)
    by_road = df.groupby("road_name")["risk_score"].mean().round(1)
    by_hour = df.groupby("hour")["accident_id"].count()
    return {
        "total_records": int(len(df)),
        "most_dangerous": by_road.idxmax(),
        "peak_hour": int(by_hour.idxmax()),
        "top_cause": df["cause"].mode()[0],
        "by_road": by_road.to_dict(),
        "by_hour": {int(k): int(v) for k, v in by_hour.items()},
        "by_weather": df.groupby("weather")["accident_id"].count().to_dict(),
        "severity_breakdown": df.groupby("severity")["accident_id"].count().to_dict(),
        "fatalities": int(df["fatalities"].sum()),
        "source": "bigquery" if bigquery_client.enabled() else "csv",
    }


@app.get("/api/stats")
def get_stats():
    if "stats" not in _stats_cache:  # data is static, compute once per process
        _stats_cache["stats"] = _compute_stats()
    return jsonify(_stats_cache["stats"])


# ---- frontend ---------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 5000)))
