import math

import pytest

from backend import road_router as rr
from backend.route_engine import get_routes

ORIGIN, DEST = (17.7000, 83.2000), (17.7000, 83.3000)

ROAD_DATA = {
    "roads": [{
        "id": "H1", "name": "Deadly Junction", "area": "x",
        "coordinates": [[17.7000, 83.2480], [17.7000, 83.2520]],  # on the direct line
        "speed_limit": 40, "road_type": "junction", "known_issues": [],
        "base_risk": 90, "peak_hours": [],
    }],
    "locations": [{"name": "A", "lat": ORIGIN[0], "lng": ORIGIN[1]},
                  {"name": "B", "lat": DEST[0], "lng": DEST[1]}],
}


def _line(points, n=40):
    out = []
    for (a, b) in zip(points, points[1:]):
        for i in range(n):
            f = i / n
            out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f])
    out.append(list(points[-1]))
    return out


def fake_osrm(points, alternatives):
    """Direct = short but through the hotspot; any detour via a side point is longer."""
    coords = _line(points)
    length_m = rr.polyline_m(coords)
    detour = len(points) > 2
    secs = length_m / 11.0 * (1.15 if detour else 1.0)
    return {"code": "Ok",
            "waypoints": [{"location": [p[1], p[0]]} for p in (points[0], points[-1])],
            "routes": [{"geometry": {"coordinates": [[c[1], c[0]] for c in coords]},
                        "duration": secs, "distance": length_m,
                        "legs": [{"steps": [{"name": "Main Rd", "distance": length_m}]}]}]}


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(rr, "_osrm_request", fake_osrm)


def test_route_is_complete_and_starts_and_ends_at_selected_points(fake):
    r = get_routes("A", "B", 12, "Clear", "Monday", ROAD_DATA, [])
    for kind in ("fastest", "safest", "recommended"):
        path = r[kind]["path"]
        assert rr.haversine_m(path[0], ORIGIN) < 5
        assert rr.haversine_m(path[-1], DEST) < 5
        assert r[kind]["origin_point"] == list(ORIGIN)
        assert r[kind]["destination_point"] == list(DEST)
        # no big jumps: every hop is short, so the line is continuous
        assert max(rr.haversine_m(a, b) for a, b in zip(path, path[1:])) < 400
    assert r["engine"] == "osrm"


def test_safest_avoids_the_hotspot_that_fastest_goes_through(fake):
    r = get_routes("A", "B", 12, "Clear", "Monday", ROAD_DATA, [])
    assert r["fastest"]["risk_score"] > r["safest"]["risk_score"]
    assert r["fastest"]["time"] <= r["safest"]["time"]
    assert [x["name"] for x in r["fastest"]["roads"]] == ["Deadly Junction"]
    assert r["safest"]["roads"] == []
    assert r["safest"]["path"] != r["fastest"]["path"]
    assert "safest" not in r["fastest"]["same_as"]


def test_closed_road_is_avoided(fake):
    ev = [{"road": "Deadly Junction", "type": "Work", "start_time": "00:00",
           "end_time": "23:59", "severity": "Full Restriction"}]
    r = get_routes("A", "B", 12, "Clear", "Monday", ROAD_DATA, ev)
    assert r["blocked_roads"] == ["Deadly Junction"]
    for k in ("fastest", "safest", "recommended"):
        d = min(rr.point_polyline_m(p, ROAD_DATA["roads"][0]["coordinates"])
                for p in r[k]["path"])
        assert d > rr.BLOCK_RADIUS_M


def test_falls_back_to_network_model_when_osrm_is_down():
    r = get_routes("A", "B", 12, "Clear", "Monday", ROAD_DATA, [])  # autouse offline
    assert r["engine"] == "network-model"
    assert "Approximate" in r["fastest"]["note"]
    assert r["fastest"]["origin_point"] == list(ORIGIN)


def test_single_route_is_flagged_not_faked(monkeypatch):
    def only_direct(points, alternatives):
        if len(points) > 2:
            raise rr.RoutingUnavailable("no detour")
        return fake_osrm(points, alternatives)
    monkeypatch.setattr(rr, "_osrm_request", only_direct)
    data = {**ROAD_DATA, "roads": []}
    r = get_routes("A", "B", 12, "Clear", "Monday", data, [])
    assert r["fastest"]["same_as"] and "Only one sensible route" in r["fastest"]["note"]


def test_resample_and_distance_helpers():
    line = _line([(17.7, 83.2), (17.7, 83.21)])
    total = rr.polyline_m(line)
    assert abs(sum(m for _, m in rr.resample(line)) - total) < rr.SAMPLE_STEP_M
    assert rr.point_segment_m([17.7005, 83.205], [17.7, 83.2], [17.7, 83.21]) == pytest.approx(
        55.6, abs=1.5)


# ---- closure / hotspot matched by REAL street name (saved coordinates are wrong) ----
def fake_osrm_named(points, alternatives):
    """Direct road has a stretch literally named 'NAD Junction'; detours never touch it."""
    detour = len(points) > 2
    coords = _line(points)
    steps = []
    if detour:
        steps.append({"name": "Side Rd", "distance": rr.polyline_m(coords),
                      "geometry": {"coordinates": [[c[1], c[0]] for c in coords]}})
    else:
        a = [c for c in coords if c[1] < 83.24]
        b = [c for c in coords if 83.24 <= c[1] <= 83.25]
        c_ = [c for c in coords if c[1] > 83.25]
        for nm, seg in (("Main Rd", a + b[:1]), ("NAD Junction", b), ("Main Rd", b[-1:] + c_)):
            steps.append({"name": nm, "distance": rr.polyline_m(seg) if len(seg) > 1 else 0,
                          "geometry": {"coordinates": [[c[1], c[0]] for c in seg]}})
    length_m = rr.polyline_m(coords)
    return {"code": "Ok",
            "waypoints": [{"location": [p[1], p[0]]} for p in (points[0], points[-1])],
            "routes": [{"geometry": {"coordinates": [[c[1], c[0]] for c in coords]},
                        "duration": length_m / 11.0 * (1.2 if detour else 1.0),
                        "distance": length_m, "legs": [{"steps": steps}]}]}


NAD_DATA = {
    "roads": [{"id": "N1", "name": "NAD Junction", "area": "x",
               "coordinates": [[17.80, 83.50], [17.8005, 83.5005]],  # WRONG place on purpose
               "speed_limit": 40, "road_type": "junction", "known_issues": [],
               "base_risk": 80, "peak_hours": []}],
    "locations": ROAD_DATA["locations"],
}
CLOSED = [{"road": "NAD Junction", "type": "Work", "start_time": "00:00",
           "end_time": "23:59", "severity": "Full Restriction"}]


def _streets(route):
    return " ".join(route["via"])


def test_closed_road_is_avoided_even_when_saved_coordinates_are_wrong(monkeypatch):
    monkeypatch.setattr(rr, "_osrm_request", fake_osrm_named)
    r = get_routes("A", "B", 11, "Clear", "Monday", NAD_DATA, CLOSED)
    for k in ("fastest", "safest", "recommended"):
        assert "NAD Junction" not in _streets(r[k]), k
        assert "note" not in r[k] or "closed road" not in r[k]["note"]
    # without the closure the direct road (through NAD Junction) is the fastest
    r2 = get_routes("A", "B", 11, "Clear", "Monday", NAD_DATA, [])
    assert "NAD Junction" in _streets(r2["fastest"])


def test_hotspot_matched_by_name_raises_risk_and_safest_detours(monkeypatch):
    monkeypatch.setattr(rr, "_osrm_request", fake_osrm_named)
    r = get_routes("A", "B", 11, "Clear", "Monday", NAD_DATA, [])
    assert [h["name"] for h in r["fastest"]["roads"]] == ["NAD Junction"]
    assert r["safest"]["roads"] == []
    assert r["safest"]["risk_score"] < r["fastest"]["risk_score"]
    assert r["safest"]["path"] != r["fastest"]["path"]


def test_dedupe_is_fast_on_long_routes():
    import time
    long = [[17.6 + i * 0.0001, 83.2 + i * 0.0001] for i in range(2500)]
    cands = [{"coords": long, "osrm_s": 1000 + i} for i in range(8)]
    t = time.time()
    assert len(rr._dedupe(cands)) == 1
    assert time.time() - t < 3


def test_routes_report_how_much_they_share_with_the_fastest(fake):
    r = get_routes("A", "B", 12, "Clear", "Monday", ROAD_DATA, [])
    assert r["fastest"]["overlap_with_fastest"] == 100
    assert 0 <= r["safest"]["overlap_with_fastest"] < 100   # safest takes a detour


def test_real_data_coordinates_are_sane():
    """Guards against the earlier wrong coordinates: every hotspot road must sit within
    500 m of the location with the same name, and everything must be inside Vizag."""
    import json
    from pathlib import Path
    d = json.loads((Path(__file__).resolve().parent.parent / "data" / "vizag_roads.json")
                   .read_text())
    for loc in d["locations"]:
        assert 17.55 < loc["lat"] < 17.90 and 83.10 < loc["lng"] < 83.40, loc["name"]
    by_name = {l["name"]: (l["lat"], l["lng"]) for l in d["locations"]}
    for road in d["roads"]:
        for key in (road["name"], road["name"].replace(" Junction", "").replace(" Road", "")
                    .replace(" Main", "").strip()):
            if key in by_name:
                dist = rr.point_polyline_m(list(by_name[key]), road["coordinates"])
                assert dist < 500, f"{road['name']} is {dist:.0f} m from location {key}"
                break


# ---- dead-end spur / out-and-back detours -------------------------------------------
def _spur_route(points):
    """Drives to the destination, keeps going 1.2 km north into a side road, then returns."""
    a, b = points[0], points[-1]
    tip = [b[0] + 0.011, b[1]]
    return _line([a, b]) + _line([b, tip])[1:] + _line([tip, b])[1:]


def test_backtrack_fraction_flags_spurs_but_not_clean_routes():
    clean = _line([ORIGIN, DEST])
    assert rr.backtrack_fraction(clean) < 0.005
    assert rr.backtrack_fraction(_spur_route([ORIGIN, DEST])) > 0.1


def test_routes_with_a_spur_past_the_destination_are_never_returned(monkeypatch):
    def osrm(points, alternatives):
        data = fake_osrm(points, alternatives)
        if len(points) > 2:  # every detour overshoots into a spur
            coords = _spur_route(points)
            data["routes"][0]["geometry"]["coordinates"] = [[c[1], c[0]] for c in coords]
            data["routes"][0]["distance"] = rr.polyline_m(coords)
            data["routes"][0]["duration"] = rr.polyline_m(coords) / 11.0
        return data
    monkeypatch.setattr(rr, "_osrm_request", osrm)
    r = get_routes("A", "B", 12, "Clear", "Monday", {**ROAD_DATA, "roads": []}, [])
    for k in ("fastest", "safest", "recommended"):
        path = r[k]["path"]
        assert rr.backtrack_fraction(path) < rr.BACKTRACK_MAX, k
        assert rr.haversine_m(path[-1], DEST) < 5, k
        assert max(p[0] for p in path) < DEST[0] + 0.001, k   # never drives past the end


# ---- route selection: three different budgets ---------------------------------------
def _c(name, minutes, risk, km):
    return {"name": name, "minutes": minutes, "risk": risk, "km": km}


def test_select_gives_three_different_routes_when_trade_offs_exist():
    cands = [_c("direct", 20, 60, 10), _c("mid", 24, 40, 12), _c("long", 33, 25, 18)]
    r = rr._select(cands)
    assert r["fastest"]["name"] == "direct"
    assert r["recommended"]["name"] == "mid"      # within +25% time, big risk cut
    assert r["safest"]["name"] == "long"          # within +75% time, lowest risk


def test_select_stays_on_fastest_when_detour_gains_almost_nothing():
    cands = [_c("direct", 20, 40, 10), _c("detour", 30, 39.6, 14)]
    r = rr._select(cands)
    assert r["fastest"] is r["recommended"] is r["safest"] is cands[0]


def test_select_prefers_a_clearly_shorter_shortcut_within_3_percent_time():
    cands = [_c("main", 20.0, 40, 12.0), _c("lanes", 20.5, 41, 9.0)]
    assert rr._select(cands)["fastest"]["name"] == "lanes"
    slow = [_c("main", 20.0, 40, 12.0), _c("lanes", 23.0, 41, 9.0)]
    assert rr._select(slow)["fastest"]["name"] == "main"


def test_select_never_picks_detours_beyond_the_time_budget():
    cands = [_c("direct", 20, 60, 10), _c("crazy", 60, 5, 40)]
    assert rr._select(cands)["safest"]["name"] == "direct"
