def calculate_risk(base_risk, hour, weather, day, peak_hours):
    risk = base_risk

    # Time of day impact
    if 20 <= hour or hour <= 5:
        risk = min(100, risk + 15)  # night = more dangerous
    elif 7 <= hour <= 10 or 17 <= hour <= 20:
        risk = min(100, risk + 8)   # peak hours

    # Weather impact
    weather_impact = {
        "Rainy": 12,
        "Foggy": 10,
        "Windy": 5,
        "Clear": 0
    }
    risk = min(100, risk + weather_impact.get(weather, 0))

    # Weekend impact
    if day in ["Friday", "Saturday", "Sunday"]:
        risk = min(100, risk + 5)

    return round(risk, 1)