import pytest

from backend import road_router


@pytest.fixture(autouse=True)
def no_network_routing(monkeypatch):
    """Tests never hit the real OSRM server; individual tests install a fake."""
    def _offline(points, alternatives):
        raise road_router.RoutingUnavailable("offline in tests")
    monkeypatch.setattr(road_router, "_osrm_request", _offline)
    road_router._CACHE.clear()
