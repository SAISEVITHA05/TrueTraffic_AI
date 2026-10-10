"""Real-road routing with safety scoring.

Why this exists
---------------
The old engine (route_engine.py) joined a handful of short, hand-drawn road
pieces with straight "connector" lines, so a route could stop mid-way and never
follow real streets. This module fixes that:

1. ROAD GEOMETRY  - candidate routes come from an OSRM routing server
   (default: the free public demo server, override with OSRM_URL). Each route is
   a full polyline that follows real roads from the origin to the destination.
   Several candidates are collected: OSRM's own alternatives plus detours via
   side waypoints, so "safest" can genuinely differ from "fastest".
2. RISK SCORING   - every candidate is sampled every ~40 m. Each sample gets the
   baseline city risk, blended towards the live risk of any known accident
   hotspot road (vizag_roads.json) it passes close to.
3. SELECTION      - fastest = least time, safest = least risk (within a sane
   time cap), recommended = 60% safety / 40% speed balance.

If OSRM cannot be reached, get_routes() in route_engine.py falls back to the old
network model so the app keeps working.
"""
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from backend.risk_model import active_events, calculate_risk, get_risk_level, is_peak

log = logging.getLogger(__name__)

OSRM_URL = os.getenv("OSRM_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_TIMEOUT_S = float(os.getenv("OSRM_TIMEOUT", "8"))

SAMPLE_STEP_M = 40.0         # risk is evaluated this often along a route
HOTSPOT_RADIUS_M = 350.0     # a hotspot road affects routes within this distance
BASELINE_ROAD_RISK = 30.0    # an ordinary road with no known hotspot nearby
BLOCK_RADIUS_M = 120.0       # a fully-closed road blocks routes passing this close
DUPLICATE_OVERLAP = 0.92     # two routes this similar are treated as the same
BACKTRACK_MAX = 0.015        # reject routes where >1.5% of the road is driven twice
HOTSPOT_PENALTY = 0.08       # risk points added per point a crossed hotspot exceeds baseline
HOTSPOT_PENALTY_CAP = 25.0
SAFEST_MINUTE_COST = 0.15    # safest: each extra minute must "buy" this much risk reduction
RECOMMENDED_MINUTE_COST = 0.30
SAFEST_TIME_RATIO = 1.75     # safest may take up to 75% longer than the fastest
RECOMMENDED_TIME_RATIO = 1.25  # recommended may take up to 25% longer
MIN_RISK_GAIN = 1.0          # a detour must cut at least this many risk points to be offered
SHORTCUT_TIME_TOLERANCE = 1.03   # a route at most 3% slower...
SHORTCUT_MIN_SAVING = 0.10       # ...but 10%+ shorter counts as the better "fastest" shortcut
CACHE_TTL_S = 600


class RoutingUnavailable(RuntimeError):
    """Raised when the OSRM server can't be reached or returns nothing usable."""


# --------------------------------------------------------------------------- geo
def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def _to_xy(p, ref_lat):
    """Local flat projection in metres (fine at city scale)."""
    return (math.radians(p[1]) * 6371000.0 * math.cos(math.radians(ref_lat)),
            math.radians(p[0]) * 6371000.0)


def point_segment_m(p, a, b):
    """Distance in metres from point p to segment a-b (all [lat, lng])."""
    ref = p[0]
    px, py = _to_xy(p, ref)
    ax, ay = _to_xy(a, ref)
    bx, by = _to_xy(b, ref)
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_polyline_m(p, coords):
    if len(coords) == 1:
        return haversine_m(p, coords[0])
    return min(point_segment_m(p, coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def polyline_m(coords):
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def resample(coords, step_m=SAMPLE_STEP_M):
    """Points every ~step_m along a polyline as (point, metres_represented)."""
    if len(coords) < 2:
        return [(coords[0], 0.0)] if coords else []
    total = polyline_m(coords)
    if total <= step_m:
        return [(coords[len(coords) // 2], total)]
    out, walked, next_at = [], 0.0, step_m / 2
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        seg = haversine_m(a, b)
        while seg > 0 and next_at <= walked + seg:
            f = (next_at - walked) / seg
            out.append(([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], step_m))
            next_at += step_m
        walked += seg
    return out or [(coords[len(coords) // 2], total)]


# ------------------------------------------------------------------------ OSRM
def _osrm_request(points, alternatives):
    """points: list of [lat, lng]. Returns the parsed OSRM JSON."""
    coord_str = ";".join(f"{p[1]:.6f},{p[0]:.6f}" for p in points)
    alt = "3" if alternatives else "false"
    url = (f"{OSRM_URL}/route/v1/driving/{coord_str}"
           f"?alternatives={alt}&overview=full&geometries=geojson&steps=true")
    req = urllib.request.Request(url, headers={"User-Agent": "TrueTrafficAI/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RoutingUnavailable(f"OSRM request failed: {exc}") from exc
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingUnavailable(f"OSRM returned {data.get('code')}")
    return data


def _parse_osrm(data):
    """Turn OSRM JSON into candidate dicts with [lat, lng] geometry."""
    snapped = [[w["location"][1], w["location"][0]] for w in data.get("waypoints", [])]
    cands = []
    for r in data["routes"]:
        coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        if len(coords) < 2:
            continue
        street_m, steps = {}, []
        for leg in r.get("legs", []):
            for st in leg.get("steps", []):
                name = (st.get("name") or "").strip()
                if name:
                    street_m[name] = street_m.get(name, 0.0) + st.get("distance", 0.0)
                geom = (st.get("geometry") or {}).get("coordinates") or []
                if len(geom) >= 2:
                    steps.append({"name": name, "coords": [[c[1], c[0]] for c in geom]})
        cands.append({"coords": coords, "osrm_s": float(r["duration"]),
                      "osrm_m": float(r["distance"]), "streets": street_m,
                      "steps": steps, "snapped": snapped})
    return cands


def _detour_waypoint_sets(po, pd_):
    """Side waypoints that nudge the router onto genuinely different roads."""
    dist = haversine_m(po, pd_)
    off = max(600.0, min(2500.0, dist * 0.30))  # metres to the side
    cos_lat = math.cos(math.radians(po[0]))
    dlat, dlng = pd_[0] - po[0], pd_[1] - po[1]
    norm = math.hypot(dlat, dlng * cos_lat) or 1e-9
    perp_lat = -dlng * cos_lat / norm              # unit perpendicular, in degrees
    perp_lng = dlat / norm / cos_lat
    m_per_deg = 111320.0
    sets = []
    for t, sign in ((0.5, 1), (0.5, -1), (0.35, 1), (0.65, -1)):
        lat = po[0] + dlat * t + sign * perp_lat * off / m_per_deg
        lng = po[1] + dlng * t + sign * perp_lng * off / m_per_deg
        sets.append([po, [lat, lng], pd_])
    # corridor routes: waypoints exactly ON the straight line pull the route towards the
    # most direct roads and lanes, which is where shorter-distance shortcuts show up
    sets.append([po, [po[0] + dlat * 0.5, po[1] + dlng * 0.5], pd_])
    sets.append([po, [po[0] + dlat * 0.33, po[1] + dlng * 0.33],
                 [po[0] + dlat * 0.67, po[1] + dlng * 0.67], pd_])
    # wide curves: two waypoints on the same side, a third and two thirds of the way
    for sign in (1, -1):
        wps = []
        for t in (0.33, 0.67):
            wps.append([po[0] + dlat * t + sign * perp_lat * off / m_per_deg,
                        po[1] + dlng * t + sign * perp_lng * off / m_per_deg])
        sets.append([po, wps[0], wps[1], pd_])
    return sets


def _fetch_sets(point_sets):
    """Route through each list of points (in parallel); failures are skipped."""
    def _via(pts):
        try:
            return _parse_osrm(_osrm_request(pts, alternatives=False))
        except RoutingUnavailable as exc:
            log.info("detour candidate skipped: %s", exc)
            return []
    out = []
    if not point_sets:
        return out
    with ThreadPoolExecutor(max_workers=4) as pool:
        for found in pool.map(_via, point_sets):
            out.extend(found)
    return out


_CACHE = {}  # (origin, destination) -> (timestamp, [raw candidates]); saves OSRM requests


def fetch_candidates(po, pd_):
    """Direct route(+alternatives) first, then corridor and side detours (cached 10 min,
    because they don't depend on time of day or weather)."""
    key = (round(po[0], 5), round(po[1], 5), round(pd_[0], 5), round(pd_[1], 5))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return [dict(c) for c in hit[1]]
    direct = _parse_osrm(_osrm_request([po, pd_], alternatives=True))
    cands = list(direct) + _fetch_sets(_detour_waypoint_sets(po, pd_))
    _CACHE[key] = (time.time(), cands)
    return [dict(c) for c in cands]


def _direction_at(coords, point, span_m=150.0):
    """Unit travel direction (east, north) of the route around `point`."""
    i = min(range(len(coords)), key=lambda k: haversine_m(coords[k], point))
    lo = hi = i
    while lo > 0 and haversine_m(coords[lo], coords[i]) < span_m:
        lo -= 1
    while hi < len(coords) - 1 and haversine_m(coords[hi], coords[i]) < span_m:
        hi += 1
    ax, ay = _to_xy(coords[lo], point[0])
    bx, by = _to_xy(coords[hi], point[0])
    n = math.hypot(bx - ax, by - ay) or 1.0
    return (bx - ax) / n, (by - ay) / n


def _avoid_sets(po, pd_, target, offsets):
    """Waypoint sets that push a route off to either side of `target`."""
    dx, dy = _direction_at(target["route"], target["point"])
    px, py = -dy, dx  # perpendicular
    m_lat = 111320.0
    m_lng = 111320.0 * math.cos(math.radians(target["point"][0]))
    sets = []
    for off in offsets:
        for sign in (1, -1):
            wp = [target["point"][0] + sign * py * off / m_lat,
                  target["point"][1] + sign * px * off / m_lng]
            sets.append([po, wp, pd_])
    return sets


# ------------------------------------------------------------------- risk model
def _hotspots(road_data, hour, weather, day, events):
    ev = active_events(events, hour)
    spots, blocked = [], []
    for road in road_data["roads"]:
        e = ev.get(road["name"])
        if e and e.get("severity") == "Full Restriction":
            blocked.append(road)
            continue
        risk = calculate_risk(road["base_risk"], hour, weather, day, road.get("peak_hours"),
                              e.get("severity") if e else None)
        spots.append({"name": road["name"], "risk": risk, "coords": road["coordinates"],
                      "event": e["type"] if e else None})
    return spots, blocked


def _norm(name):
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (name or "").lower()).split())


def name_matches(step_name, road_name):
    """Does an OSM street name refer to one of our named hotspot/closed roads?"""
    a, b = _norm(step_name), _norm(road_name)
    if not a or not b:
        return False
    return a == b or (len(b) >= 6 and b in a) or (len(a) >= 6 and a in b)


def _sample_risk(p, spots, baseline):
    """Baseline blended towards the nearest hotspot's risk by closeness."""
    best_w, best_risk = 0.0, baseline
    for s in spots:
        d = point_polyline_m(p, s["coords"])
        if d < HOTSPOT_RADIUS_M:
            w = 1.0 - d / HOTSPOT_RADIUS_M
            if w > best_w:
                best_w, best_risk = w, s["risk"]
    return baseline * (1 - best_w) + best_risk * best_w


def _tagged_samples(cand):
    """[(point, metres, street_name)] along the whole route."""
    out = []
    for st in cand.get("steps") or []:
        for p, m in resample(st["coords"]):
            out.append((p, m, st["name"]))
    if not out:
        out = [(p, m, "") for p, m in resample(cand["coords"])]
    return out


def _middle(points):
    return points[len(points) // 2]


def score_candidate(cand, spots, baseline, blocked, hour, weather, peak_factor):
    samples = _tagged_samples(cand)
    total = sum(m for _, m, _ in samples) or 1.0

    # per-sample risk: coordinate proximity OR the street's name matching a hotspot
    risk_sum = 0.0
    name_hits = {}          # hotspot name -> [sample points whose street name matches]
    for p, m, street in samples:
        r = _sample_risk(p, spots, baseline)
        for s in spots:
            if name_matches(street, s["name"]):
                r = max(r, s["risk"])
                name_hits.setdefault(s["name"], []).append(p)
        risk_sum += r * m
    risk = risk_sum / total

    crossed = []
    for s in spots:
        d = min((point_polyline_m(p, s["coords"]) for p, _, _ in samples), default=1e9)
        hits = name_hits.get(s["name"])
        if d <= HOTSPOT_RADIUS_M * 0.6 or hits:
            point = _middle(hits) if hits else min(
                (p for p, _, _ in samples), key=lambda q: point_polyline_m(q, s["coords"]))
            crossed.append({"name": s["name"], "risk": s["risk"], "coords": s["coords"],
                            "event": s["event"], "point": point,
                            "by_name": bool(hits), "distance_m": round(d)})
    crossed.sort(key=lambda x: -x["risk"])

    blocked_hits, blocked_points = [], []
    for b in blocked:
        pts = [p for p, _, street in samples if name_matches(street, b["name"])]
        if not pts:
            pts = [p for p, _, _ in samples
                   if point_polyline_m(p, b["coordinates"]) <= BLOCK_RADIUS_M]
        if pts:
            blocked_hits.append(b["name"])
            blocked_points.append({"name": b["name"], "point": _middle(pts),
                                   "route": cand["coords"]})

    # Averaging over 15+ km dilutes a 100 m accident hotspot, so each hotspot a route
    # actually passes also adds an exposure penalty proportional to how dangerous it is.
    exposure = sum(max(0.0, h["risk"] - baseline) * HOTSPOT_PENALTY for h in crossed)
    risk = min(100.0, risk + min(HOTSPOT_PENALTY_CAP, exposure))

    minutes = cand["osrm_s"] / 60.0 * peak_factor
    cand["backtrack"] = backtrack_fraction(cand["coords"])
    cand.update({"risk": round(risk, 1), "minutes": minutes, "km": cand["osrm_m"] / 1000.0,
                 "crossed": crossed, "blocked_hits": blocked_hits,
                 "blocked_points": blocked_points})
    return cand


def backtrack_fraction(coords, tol_m=25.0, min_gap_m=150.0, step_m=20.0):
    """Share of a route lying on road it already drove earlier (out-and-back spurs)."""
    pts = [p for p, _ in resample(coords, step_m)]
    if len(pts) < 20:
        return 0.0
    ref = pts[0][0]
    cell = tol_m * 2
    xy = [_to_xy(p, ref) for p in pts]
    grid = {}
    for i, (x, y) in enumerate(xy):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    gap = int(min_gap_m / step_m)
    repeated = 0
    for i, (x, y) in enumerate(xy):
        cx, cy = int(x // cell), int(y // cell)
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    if abs(i - j) > gap and math.hypot(x - xy[j][0], y - xy[j][1]) <= tol_m:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        repeated += hit
    return repeated / len(xy)


def _grid(cand, cell_m=60.0):
    """Spatial hash of the route densified to ~20 m, built once per candidate."""
    if "_grid" not in cand:
        ref = cand["coords"][0][0]
        g = {}
        dense = [p for p, _ in resample(cand["coords"], 20.0)] + [cand["coords"][0],
                                                                  cand["coords"][-1]]
        for p in dense:
            x, y = _to_xy(p, ref)
            g.setdefault((int(x // cell_m), int(y // cell_m)), []).append((x, y))
        cand["_grid"] = (ref, cell_m, g)
    return cand["_grid"]


def _overlap(a, b, tol_m=60.0):
    """Share of a's route lying within ~tol_m of b's route."""
    ref, cell, g = _grid(b)
    pts = [p for p, _ in resample(a["coords"], 80.0)]
    if not pts:
        return 1.0
    near = 0
    for p in pts:
        x, y = _to_xy(p, ref)
        cx, cy = int(x // cell), int(y // cell)
        if any(math.hypot(x - qx, y - qy) <= tol_m
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for qx, qy in g.get((cx + dx, cy + dy), ())):
            near += 1
    return near / len(pts)


def _dedupe(cands):
    kept = []
    for c in sorted(cands, key=lambda x: x["osrm_s"]):
        if any(_overlap(c, k) >= DUPLICATE_OVERLAP and _overlap(k, c) >= DUPLICATE_OVERLAP
               for k in kept):
            continue
        kept.append(c)
    return kept


def _peak_factor(hour, weather):
    f = 1.0
    if 7 <= hour <= 10 or 17 <= hour <= 20:
        f *= 1.25
    if weather in ("Rainy", "Foggy"):
        f *= 1.15
    return f


def _select(cands):
    """Three different budgets:
    fastest     - least time (a clearly shorter near-tie wins: that is the 'shortcut')
    recommended - safest route costing at most 25% extra time
    safest      - safest route costing at most 75% extra time
    A detour is only offered if it cuts at least MIN_RISK_GAIN risk points, otherwise the
    option simply stays on the fastest road (and the card says so)."""
    fastest = min(cands, key=lambda c: (c["minutes"], c["km"]))
    near = [c for c in cands
            if c["minutes"] <= fastest["minutes"] * SHORTCUT_TIME_TOLERANCE
            and c["km"] <= fastest["km"] * (1 - SHORTCUT_MIN_SAVING)]
    if near:
        fastest = min(near, key=lambda c: (c["minutes"], c["km"]))
    tmin = fastest["minutes"]

    def pick(max_ratio, minute_cost):
        ok = [c for c in cands if c["minutes"] <= tmin * max_ratio + 2]
        best = min(ok, key=lambda c: (c["risk"] + minute_cost * max(0.0, c["minutes"] - tmin),
                                      c["minutes"]))
        return best if fastest["risk"] - best["risk"] >= MIN_RISK_GAIN else fastest

    recommended = pick(RECOMMENDED_TIME_RATIO, RECOMMENDED_MINUTE_COST)
    safest = pick(SAFEST_TIME_RATIO, SAFEST_MINUTE_COST)
    if recommended["risk"] < safest["risk"]:   # safest can never be riskier than recommended
        safest = recommended
    return {"fastest": fastest, "safest": safest, "recommended": recommended}


LABELS = {"fastest": "Fastest Route", "safest": "Safest Route",
          "recommended": "Recommended Route"}


def _summarise(kind, c, origin, destination, po, pd_, geom_id):
    coords = c["coords"]
    snapped = c.get("snapped") or []
    start_snap = snapped[0] if snapped else coords[0]
    end_snap = snapped[-1] if snapped else coords[-1]
    top_streets = [n for n, _ in sorted(c["streets"].items(), key=lambda kv: -kv[1])[:4]]
    return {
        "name": LABELS[kind], "type": kind, "origin": origin, "destination": destination,
        "path": coords,
        "roads": [{"name": h["name"], "risk": h["risk"], "coords": h["coords"],
                   "event": h["event"]} for h in c["crossed"]],
        "via": top_streets,
        "origin_point": list(po), "destination_point": list(pd_),
        "origin_snapped": start_snap, "destination_snapped": end_snap,
        "risk_score": c["risk"], "risk_level": get_risk_level(c["risk"]),
        "time": max(1, round(c["minutes"])), "distance": round(c["km"], 1),
        "smart_score": 0, "geometry_id": geom_id,
    }


def _avoid_targets(cands, po, pd_, max_hotspots=2):
    """Places the routes should detour around: closed roads first, then hotspots."""
    out, seen = [], set()
    for c in cands:
        for t in c["blocked_points"]:
            if t["name"] not in seen:
                seen.add(t["name"])
                out.append({**t, "kind": "closed"})
    fastest = min(cands, key=lambda c: c["minutes"])
    n = 0
    for h in fastest["crossed"]:
        if h["name"] in seen or n >= max_hotspots:
            continue
        if haversine_m(h["point"], po) < 600 or haversine_m(h["point"], pd_) < 600:
            continue  # right at the start/end: no detour can avoid it
        seen.add(h["name"])
        n += 1
        out.append({"name": h["name"], "point": h["point"], "route": fastest["coords"],
                    "kind": "hotspot"})
    return out


def get_real_routes(origin, destination, hour, weather, day, road_data, events, po, pd_):
    """Return the same structure as route_engine.get_routes(), on real roads."""
    spots, blocked = _hotspots(road_data, hour, weather, day, events)
    pf = _peak_factor(hour, weather)

    def score_all(raw):
        return [score_candidate(c, spots, BASELINE_ROAD_RISK, blocked, hour, weather, pf)
                for c in raw]

    cands = score_all(fetch_candidates(po, pd_))
    if not cands:
        raise RoutingUnavailable("No candidate routes")

    # Round 1: detour around closed roads / risky hotspots found on the routes so far
    sets = []
    for t in _avoid_targets(cands, po, pd_):
        sets += _avoid_sets(po, pd_, t, (900, 1800) if t["kind"] == "closed" else (900,))
    cands += score_all(_fetch_sets(sets))

    # Round 2: every route still hits a closed road -> try much wider detours
    if all(c["blocked_hits"] for c in cands):
        sets = []
        for t in _avoid_targets(cands, po, pd_, max_hotspots=0):
            sets += _avoid_sets(po, pd_, t, (3000, 4500))
        cands += score_all(_fetch_sets(sets))

    # drop detours that drive out to a dead-end spur and back (keep the direct route always)
    first = cands[0]
    cands = [first] + [c for c in cands[1:] if c["backtrack"] <= BACKTRACK_MAX]
    cands = _dedupe(cands)

    usable = [c for c in cands if not c["blocked_hits"]]
    closed_note = None
    if usable:
        cands = usable
    else:
        names = sorted({n for c in cands for n in c["blocked_hits"]})
        closed_note = (f"Every route I could find passes a closed road ({', '.join(names)}); "
                       "showing the least-bad option.")

    chosen = _select(cands)
    geom_ids = {}
    routes = {}
    for kind, c in chosen.items():
        gid = geom_ids.setdefault(id(c), len(geom_ids) + 1)
        routes[kind] = _summarise(kind, c, origin, destination, po, pd_, gid)

    t_min = min(r["time"] for r in routes.values())
    for r in routes.values():
        time_score = 100.0 * t_min / r["time"]
        r["smart_score"] = round((100 - r["risk_score"]) * 0.60 + time_score * 0.40, 1)

    # how much of each route lies on the fastest route's roads (explains overlapping lines)
    for k, r in routes.items():
        r["overlap_with_fastest"] = (100 if k == "fastest" else
                                     round(100 * _overlap(chosen[k], chosen["fastest"])))

    # tell the UI which routes share exactly the same path
    by_gid = {}
    for k, r in routes.items():
        by_gid.setdefault(r["geometry_id"], []).append(k)
    for k, r in routes.items():
        r["same_as"] = [o for o in by_gid[r["geometry_id"]] if o != k]

    notes = []
    if len(by_gid) == 1:
        notes.append("Only one sensible route exists between these two points, so all three "
                     "options follow the same road.")
    if closed_note:
        notes.append(closed_note)
    if notes:
        for r in routes.values():
            r["note"] = " ".join(notes)

    routes["blocked_roads"] = [b["name"] for b in blocked]
    routes["engine"] = "osrm"
    routes["method"] = ("Exact shortest-time search (OSRM) over OpenStreetMap roads using speed "
                        "limits - no live traffic, and lanes missing from OpenStreetMap are unknown.")
    routes["candidates_considered"] = len(cands)
    return routes
