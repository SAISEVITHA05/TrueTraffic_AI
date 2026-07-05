import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
import os

roads = [
    "Beach Road", "Hanumanthawaka Junction", "Gajuwaka Junction",
    "NAD Junction", "Steel Plant Road", "MVP Colony Main Road",
    "Kommadi Road", "NH16 Bypass", "Dwaraka Nagar Road", "Siripuram Junction"
]

causes = [
    "Speeding", "Signal Jumping", "Wrong Side Driving",
    "Drunk Driving", "Poor Visibility", "Pothole",
    "Heavy Vehicle", "Two Wheeler Skid", "Pedestrian"
]

weather_conditions = ["Clear", "Rainy", "Foggy", "Windy"]
severity_levels = ["Minor", "Moderate", "Severe", "Fatal"]
days_of_week = ["Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday", "Sunday"]

road_base_risk = {
    "Beach Road": 75,
    "Hanumanthawaka Junction": 85,
    "Gajuwaka Junction": 78,
    "NAD Junction": 70,
    "Steel Plant Road": 72,
    "MVP Colony Main Road": 45,
    "Kommadi Road": 65,
    "NH16 Bypass": 30,
    "Dwaraka Nagar Road": 50,
    "Siripuram Junction": 60
}

rows = []
start_date = datetime(2022, 1, 1)

print("Generating 50,000 accident records for Vizag...")

for i in range(50000):
    road = random.choice(roads)
    base_risk = road_base_risk[road]
    date = start_date + timedelta(days=random.randint(0, 900))
    hour = random.randint(0, 23)
    weather = random.choice(weather_conditions)

    # Risk goes up at night and in rain
    risk_score = base_risk
    if hour >= 20 or hour <= 5:
        risk_score = min(100, risk_score + 15)
    if weather == "Rainy":
        risk_score = min(100, risk_score + 10)
    if weather == "Foggy":
        risk_score = min(100, risk_score + 8)
    if date.strftime("%A") in ["Friday", "Saturday", "Sunday"]:
        risk_score = min(100, risk_score + 5)

    rows.append({
        "accident_id": f"VZG{i:05d}",
        "road_name": road,
        "date": date.strftime("%Y-%m-%d"),
        "day_of_week": date.strftime("%A"),
        "hour": hour,
        "weather": weather,
        "cause": random.choice(causes),
        "severity": random.choice(severity_levels),
        "vehicles_involved": random.randint(1, 4),
        "injuries": random.randint(0, 8),
        "fatalities": random.choices([0, 1, 2], weights=[85, 12, 3])[0],
        "risk_score": risk_score,
        "base_risk": base_risk
    })

df = pd.DataFrame(rows)

os.makedirs("../data", exist_ok=True)
df.to_csv("../data/vizag_accidents.csv", index=False)

print(f"✅ Dataset created: {len(df)} records")
print(f"📁 Saved to: data/vizag_accidents.csv")
print("\nRoad accident counts:")
print(df["road_name"].value_counts())
print("\nSeverity breakdown:")
print(df["severity"].value_counts())