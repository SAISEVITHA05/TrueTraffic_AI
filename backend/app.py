from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import os
import json
import pandas as pd

load_dotenv()

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('../frontend', path)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

with open("data/vizag_roads.json", "r") as f:
    road_data = json.load(f)

with open("data/special_events.json", "r") as f:
    events_data = json.load(f)

from backend.risk_model import calculate_risk
from backend.route_engine import get_routes
from backend.gemini_agent import get_ai_explanation

def get_risk_level(score):
    if score >= 75: return "CRITICAL"
    elif score >= 55: return "HIGH"
    elif score >= 35: return "MEDIUM"
    else: return "LOW"

@app.route("/api/roads", methods=["GET","OPTIONS"])
def get_roads():
    return jsonify(road_data)

@app.route("/api/risk", methods=["GET","OPTIONS"])
def get_risk_scores():
    hour = int(request.args.get("hour", 12))
    weather = request.args.get("weather", "Clear")
    day = request.args.get("day", "Monday")
    roads = road_data["roads"]
    results = []
    for road in roads:
        risk = calculate_risk(
            base_risk=road["base_risk"],
            hour=hour,
            weather=weather,
            day=day,
            peak_hours=road["peak_hours"]
        )
        results.append({
            "id": road["id"],
            "name": road["name"],
            "area": road["area"],
            "coordinates": road["coordinates"],
            "risk_score": risk,
            "risk_level": get_risk_level(risk),
            "speed_limit": road["speed_limit"],
            "known_issues": road["known_issues"]
        })
    return jsonify({"roads": results, "hour": hour, "weather": weather})

@app.route("/api/routes", methods=["POST","OPTIONS"])
def find_routes():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.json
    origin = data.get("origin")
    destination = data.get("destination")
    hour = int(data.get("hour", 12))
    weather = data.get("weather", "Clear")
    day = data.get("day", "Monday")
    routes = get_routes(
        origin=origin,
        destination=destination,
        hour=hour,
        weather=weather,
        day=day,
        road_data=road_data
    )
    explanation = get_ai_explanation(
        origin=origin,
        destination=destination,
        routes=routes,
        hour=hour,
        weather=weather
    )
    return jsonify({
        "origin": origin,
        "destination": destination,
        "routes": routes,
        "ai_explanation": explanation
    })

@app.route("/api/events", methods=["GET","OPTIONS"])
def get_events():
    with open("data/special_events.json", "r") as f:
        events = json.load(f)
    return jsonify(events)

@app.route("/api/events", methods=["POST","OPTIONS"])
def add_event():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.json
    if data.get("password") != "admin123":
        return jsonify({"error": "Unauthorized"}), 401
    event = {
        "road": data["road"],
        "type": data["type"],
        "start_time": data["start_time"],
        "end_time": data["end_time"],
        "severity": data["severity"],
        "description": data.get("description", "")
    }
    with open("data/special_events.json", "r") as f:
        events = json.load(f)
    events["events"].append(event)
    with open("data/special_events.json", "w") as f:
        json.dump(events, f, indent=2)
    return jsonify({"success": True, "event": event})

@app.route("/api/events/clear", methods=["POST","OPTIONS"])
def clear_events():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    data = request.json
    if data.get("password") != "admin123":
        return jsonify({"error": "Unauthorized"}), 401
    with open("data/special_events.json", "w") as f:
        json.dump({"events": []}, f)
    return jsonify({"success": True})

@app.route("/api/stats", methods=["GET","OPTIONS"])
def get_stats():
    df = pd.read_csv("data/vizag_accidents.csv")
    stats = {
        "total_records": len(df),
        "most_dangerous": df.groupby("road_name")["risk_score"].mean().idxmax(),
        "peak_hour": int(df.groupby("hour")["accident_id"].count().idxmax()),
        "top_cause": df["cause"].mode()[0],
        "by_road": df.groupby("road_name")["risk_score"].mean().round(1).to_dict(),
        "by_hour": df.groupby("hour")["accident_id"].count().to_dict(),
        "by_weather": df.groupby("weather")["accident_id"].count().to_dict(),
        "severity_breakdown": df.groupby("severity")["accident_id"].count().to_dict()
    }
    return jsonify(stats)

if __name__ == "__main__":
    app.run(debug=True, port=5000)