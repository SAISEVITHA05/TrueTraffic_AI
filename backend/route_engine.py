import json

def get_routes(origin, destination, hour, weather, day, road_data):
    from backend.risk_model import calculate_risk

    roads = road_data["roads"]
    locations = {loc["name"]: loc for loc in road_data["locations"]}

    # Calculate live risk for all roads
    road_risks = {}
    for road in roads:
        risk = calculate_risk(
            base_risk=road["base_risk"],
            hour=hour,
            weather=weather,
            day=day,
            peak_hours=road["peak_hours"]
        )
        road_risks[road["name"]] = {
            "risk": risk,
            "id": road["id"],
            "coords": road["coordinates"]
        }

    # Define 3 route options between any origin/destination
    # In real app these would come from a routing API
    # For demo we use predefined logical Vizag routes

    route_options = generate_route_options(
        origin, destination, road_risks, hour
    )

    return route_options


def generate_route_options(origin, destination, road_risks, hour):

    # Get average risk across all roads for context
    all_risks = [v["risk"] for v in road_risks.values()]
    avg_risk = sum(all_risks) / len(all_risks)

    # Build 3 routes with different risk/time tradeoffs
    fastest = build_route(
        name="Fastest Route",
        type="fastest",
        roads=["Beach Road", "Siripuram Junction"],
        road_risks=road_risks,
        time_multiplier=1.0,
        base_time=18,
        origin=origin,
        destination=destination
    )

    safest = build_route(
        name="Safest Route",
        type="safest",
        roads=["NH16 Bypass", "MVP Colony Main Road"],
        road_risks=road_risks,
        time_multiplier=1.7,
        base_time=18,
        origin=origin,
        destination=destination
    )

    recommended = build_route(
        name="Recommended Route",
        type="recommended",
        roads=["NAD Junction", "Dwaraka Nagar Road"],
        road_risks=road_risks,
        time_multiplier=1.3,
        base_time=18,
        origin=origin,
        destination=destination
    )

    # Smart score = 60% safety + 40% speed
    for route in [fastest, safest, recommended]:
        time_score = 100 - ((route["time"] - 15) / 20 * 100)
        safety_score = 100 - route["risk_score"]
        route["smart_score"] = round(
            safety_score * 0.60 + time_score * 0.40, 1
        )

    return {
        "fastest": fastest,
        "safest": safest,
        "recommended": recommended
    }


def build_route(name, type, roads, road_risks,
                time_multiplier, base_time, origin, destination):

    # Calculate average risk for roads on this route
    route_road_risks = []
    road_details = []

    for road_name in roads:
        if road_name in road_risks:
            risk = road_risks[road_name]["risk"]
            route_road_risks.append(risk)
            road_details.append({
                "name": road_name,
                "risk": risk,
                "coords": road_risks[road_name]["coords"]
            })

    avg_risk = round(
        sum(route_road_risks) / len(route_road_risks)
        if route_road_risks else 50, 1
    )

    travel_time = round(base_time * time_multiplier)

    return {
        "name": name,
        "type": type,
        "origin": origin,
        "destination": destination,
        "roads": road_details,
        "risk_score": avg_risk,
        "risk_level": get_risk_level(avg_risk),
        "time": travel_time,
        "distance": round(travel_time * 0.6, 1),
        "smart_score": 0
    }


def get_risk_level(score):
    if score >= 75:
        return "CRITICAL"
    elif score >= 55:
        return "HIGH"
    elif score >= 35:
        return "MEDIUM"
    else:
        return "LOW"