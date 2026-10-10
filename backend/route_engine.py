"""Origin/destination-aware route engine.

The road network is modelled as a graph. Every named road in vizag_roads.json
contributes two end-point nodes joined by an edge that carries that road's live
risk. Nearby end-points (and the user's origin/destination) are linked with
generic "connector" edges. Dijkstra then finds three different routes by
optimising different costs:

  fastest      -> minimise travel time
  safest       -> strongly penalise risk exposure
  recommended  -> balanced time/risk (60% safety, 40% speed weighting)

NOTE: this is a model on top of a small hand-curated network, not a turn-by-turn
router. For production, swap `_build_graph` for OSM / Google Routes data.
"""
import heapq
import logging
import math
import os

from backend.risk_model import (
    active_events, calculate_risk, get_risk_level, is_peak,
)
from backend.road_router import RoutingUnavailable, get_real_routes

log = logging.getLogger(__name__)

CONNECTOR_RADIUS_KM = 4.5
CONNECTOR_SPEED_KMH = 22.0
CONNECTOR_BASE_RISK = 55.0
DETOUR_FACTOR = 1.4
ATTACH_K = 2  # origin/destination attach to this many nearest nodes


class RouteError(ValueError):
    pass


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(h))


def _polyline_km(coords):
    return sum(haversine_km(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _build_graph(road_data, hour, weather, day, events):
    """Return (nodes, adjacency). nodes: id -> (lat, lng)."""
    ev = active_events(events, hour)
    nodes, adj, blocked = {}, {}, []

    def add_edge(u, v, edge):
        adj.setdefault(u, []).append((v, edge))
        adj.setdefault(v, []).append((u, {**edge, "coords": list(reversed(edge["coords"]))}))

    for road in road_data["roads"]:
        e = ev.get(road["name"])
        if e and e.get("severity") == "Full Restriction":
            blocked.append(road["name"])
            continue
        coords = road["coordinates"]
        risk = calculate_risk(road["base_risk"], hour, weather, day,
                              road.get("peak_hours"), e.get("severity") if e else None)
        speed = float(road["speed_limit"])
        if is_peak(hour, road.get("peak_hours")):
            speed *= 0.65  # congestion
        if weather in ("Rainy", "Foggy"):
            speed *= 0.85
        u, v = f"{road['id']}:a", f"{road['id']}:b"
        nodes[u], nodes[v] = tuple(coords[0]), tuple(coords[-1])
        length = max(_polyline_km(coords), 0.05)
        add_edge(u, v, {
            "kind": "road", "name": road["name"], "risk": risk, "km": length,
            "min": length / speed * 60, "coords": coords,
            "event": e["type"] if e else None,
        })
    return nodes, adj, blocked


def _connect(nodes, adj, point_id, point):
    """Link a point to its nearest nodes with connector edges."""
    dists = sorted((haversine_km(point, xy), nid) for nid, xy in nodes.items())
    for d, nid in dists[:ATTACH_K]:
        _add_connector(adj, point_id, nid, point, nodes[nid], d)


def _add_connector(adj, u, v, pu, pv, d):
    km = max(d * DETOUR_FACTOR, 0.05)
    edge = {"kind": "connector", "name": None, "risk": CONNECTOR_BASE_RISK, "km": km,
            "min": km / CONNECTOR_SPEED_KMH * 60, "coords": [list(pu), list(pv)]}
    adj.setdefault(u, []).append((v, edge))
    adj.setdefault(v, []).append((u, {**edge, "coords": [list(pv), list(pu)]}))


def _dijkstra(adj, src, dst, cost_fn):
    dist, prev = {src: 0.0}, {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, edge in adj.get(u, []):
            nd = d + cost_fn(edge)
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, (u, edge)
                heapq.heappush(pq, (nd, v))
    if dst not in prev and src != dst:
        return None
    path, cur = [], dst
    while cur != src:
        u, edge = prev[cur]
        path.append(edge)
        cur = u
    return list(reversed(path))


COST_FUNCS = {
    "fastest": lambda e: e["min"],
    "safest": lambda e: e["min"] * (0.5 + 3.0 * (e["risk"] / 100.0) ** 2),
    "recommended": lambda e: e["min"] * (1.0 + 1.5 * (e["risk"] / 100.0)),
}
LABELS = {"fastest": "Fastest Route", "safest": "Safest Route",
          "recommended": "Recommended Route"}


def _summarise(kind, edges, origin, destination):
    km = sum(e["km"] for e in edges)
    minutes = sum(e["min"] for e in edges)
    risk = sum(e["risk"] * e["km"] for e in edges) / km if km else 0.0
    risk = round(risk, 1)
    path = []
    for e in edges:
        for c in e["coords"]:
            if not path or path[-1] != list(c):
                path.append(list(c))
    named = []
    for e in edges:
        if e["kind"] == "road" and (not named or named[-1]["name"] != e["name"]):
            named.append({"name": e["name"], "risk": e["risk"], "coords": e["coords"],
                          "event": e.get("event")})
    return {
        "name": LABELS[kind], "type": kind, "origin": origin, "destination": destination,
        "roads": named, "path": path, "risk_score": risk, "risk_level": get_risk_level(risk),
        "time": max(1, round(minutes)), "distance": round(km, 1), "smart_score": 0,
    }


def _bridge_components(nodes, adj):
    """Join disconnected clusters via the shortest cross-cluster link (MST-style)."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, edges in adj.items():
        for v, _ in edges:
            if u in parent and v in parent:
                parent[find(u)] = find(v)
    pairs = sorted((haversine_km(nodes[u], nodes[v]), u, v)
                   for u in nodes for v in nodes if u < v)
    for d, u, v in pairs:
        if find(u) != find(v):
            _add_connector(adj, u, v, nodes[u], nodes[v], d)
            parent[find(u)] = find(v)


def get_routes(origin, destination, hour, weather, day, road_data, events=None):
    """Real-road routing first (road_router); old network model as a fallback."""
    locs = {l["name"]: l for l in road_data["locations"]}
    if origin not in locs or destination not in locs:
        raise RouteError("Unknown origin or destination")
    if origin == destination:
        raise RouteError("Origin and destination must be different")

    po = (locs[origin]["lat"], locs[origin]["lng"])
    pd_ = (locs[destination]["lat"], locs[destination]["lng"])
    if os.getenv("ROUTING_ENGINE", "osrm").lower() != "network":
        try:
            return get_real_routes(origin, destination, hour, weather, day,
                                   road_data, events or [], po, pd_)
        except RoutingUnavailable as exc:
            log.warning("Real-road routing unavailable (%s); using network model", exc)
    routes = _get_network_routes(origin, destination, hour, weather, day, road_data, events)
    for r in routes.values():
        if isinstance(r, dict):
            r["origin_point"], r["destination_point"] = list(po), list(pd_)
            r["same_as"] = []
            r.setdefault("via", [])
            r["note"] = ("Approximate route on the built-in road model (live road routing "
                         "is unavailable). " + r.get("note", "")).strip()
    routes["engine"] = "network-model"
    return routes


def _get_network_routes(origin, destination, hour, weather, day, road_data, events=None):
    locs = {l["name"]: l for l in road_data["locations"]}

    nodes, adj, blocked = _build_graph(road_data, hour, weather, day, events or [])
    if not nodes:
        raise RouteError("All roads are currently restricted")

    po = (locs[origin]["lat"], locs[origin]["lng"])
    pd_ = (locs[destination]["lat"], locs[destination]["lng"])

    # connectors between road end-points: everything within the radius, plus each
    # node's 3 nearest neighbours so the network is always connected.
    ids = list(nodes)
    linked = set()
    for u in ids:
        others = sorted((haversine_km(nodes[u], nodes[v]), v) for v in ids
                        if v != u and v.split(":")[0] != u.split(":")[0])
        for rank, (d, v) in enumerate(others):
            if d <= CONNECTOR_RADIUS_KM or rank < 3:
                key = tuple(sorted((u, v)))
                if key not in linked:
                    linked.add(key)
                    _add_connector(adj, u, v, nodes[u], nodes[v], d)

    _bridge_components(nodes, adj)

    _connect(nodes, adj, "ORIGIN", po)
    _connect(nodes, adj, "DEST", pd_)
    nodes_all = {**nodes, "ORIGIN": po, "DEST": pd_}
    adj.setdefault("ORIGIN", []); adj.setdefault("DEST", [])
    # direct link in case origin/destination are very close
    d = haversine_km(po, pd_)
    if d <= CONNECTOR_RADIUS_KM:
        _add_connector(adj, "ORIGIN", "DEST", po, pd_, d)

    routes = {}
    for kind, fn in COST_FUNCS.items():
        edges = _dijkstra(adj, "ORIGIN", "DEST", fn)
        if edges is None:
            raise RouteError("No route found between these locations")
        routes[kind] = _summarise(kind, edges, origin, destination)

    t_min = min(r["time"] for r in routes.values())
    for r in routes.values():
        time_score = 100.0 * t_min / r["time"]  # 100 = fastest option
        r["smart_score"] = round((100 - r["risk_score"]) * 0.60 + time_score * 0.40, 1)
    sigs = {tuple(a["name"] for a in r["roads"]) + (r["time"],) for r in routes.values()}
    if len(sigs) == 1:
        for r in routes.values():
            r["note"] = "Only one sensible route exists between these points on the mapped network."
    routes["blocked_roads"] = blocked
    return routes
