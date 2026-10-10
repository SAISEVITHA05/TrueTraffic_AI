"""Live risk scoring for road segments.

risk = base road risk + time-of-day + weather + weekend adjustments (+ admin events),
clamped to 0-100.
"""
from datetime import datetime

WEATHER_IMPACT = {"Rainy": 12, "Foggy": 10, "Windy": 5, "Clear": 0}
WEEKEND_DAYS = {"Friday", "Saturday", "Sunday"}

# Extra risk when an admin event is active on a road (non-blocking severities).
EVENT_RISK_BOOST = {"Partial": 20, "Caution": 10}


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_time_window(hour: int, window: str) -> bool:
    """True if `hour` (0-23) falls inside 'HH:MM-HH:MM'. Windows may wrap midnight."""
    start_s, end_s = window.split("-")
    start, end = _to_minutes(start_s), _to_minutes(end_s)
    t = hour * 60
    if start <= end:
        return start <= t < end or (t == start)
    return t >= start or t < end


def is_peak(hour: int, peak_hours) -> bool:
    return any(in_time_window(hour, w) for w in (peak_hours or []))


def calculate_risk(base_risk, hour, weather, day, peak_hours=None, event_severity=None):
    risk = float(base_risk)

    if hour >= 20 or hour <= 5:
        risk += 15  # night
    elif is_peak(hour, peak_hours) if peak_hours else (7 <= hour <= 10 or 17 <= hour <= 20):
        risk += 8   # road-specific peak hours (generic fallback if none given)

    risk += WEATHER_IMPACT.get(weather, 0)

    if day in WEEKEND_DAYS:
        risk += 5

    if event_severity:
        risk += EVENT_RISK_BOOST.get(event_severity, 0)

    return round(max(0.0, min(100.0, risk)), 1)


def get_risk_level(score: float) -> str:
    if score >= 75:
        return "CRITICAL"
    if score >= 55:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def active_events(events, hour):
    """Events whose time window covers `hour`, keyed by road name (worst severity wins)."""
    order = {"Full Restriction": 3, "Partial": 2, "Caution": 1}
    out = {}
    for e in events:
        try:
            if not in_time_window(hour, f"{e['start_time']}-{e['end_time']}"):
                continue
        except (KeyError, ValueError):
            continue
        cur = out.get(e["road"])
        if cur is None or order.get(e.get("severity"), 0) > order.get(cur["severity"], 0):
            out[e["road"]] = e
    return out


def current_hour() -> int:
    return datetime.now().hour
