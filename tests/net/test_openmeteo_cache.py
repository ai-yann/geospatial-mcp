"""Elevation is cached for a year and forecasts for ten minutes, on one host.

Open-Meteo serves both from api.open-meteo.com, so the per-host lifetime in
``net/cache.py`` cannot tell them apart. Elevation used to inherit the forecast
lifetime, which meant a terrain profile went back to the network every ten
minutes and could never be fetched ahead of time.

These run the real client and the real sqlite cache against a fake upstream,
with the cache's clock moved forward by hand.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import httpx2 as httpx
import pytest

from geospatial_mcp.config import Settings
from geospatial_mcp.net import cache as cache_module
from geospatial_mcp.net.cache import ResponseCache
from geospatial_mcp.net.client import HttpClient
from geospatial_mcp.net.providers import openmeteo
from geospatial_mcp.net.ratelimit import RateLimiter

DAY = 86400.0
CALAMORRO = (36.6417, -4.5926)


class _Upstream:
    """A stand-in for api.open-meteo.com that counts the requests reaching it."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("/v1/elevation"):
            count = len(request.url.params["latitude"].split(","))
            return httpx.Response(200, json={"elevation": [771.0] * count})
        return httpx.Response(200, json={"current": {"temperature_2m": 21.4}})


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    fake = _Upstream()
    client = HttpClient(
        Settings.from_env({}),
        cache=ResponseCache(tmp_path / "http.sqlite3"),
        rate_limiter=RateLimiter({}),
        transport=httpx.MockTransport(fake),
    )
    monkeypatch.setattr(openmeteo, "get_client", lambda: client)
    yield fake
    client.close()


@pytest.fixture
def clock(monkeypatch):
    """The cache's notion of now, which a test can move forward."""
    now = SimpleNamespace(value=time.time())
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: now.value))
    return now


def _lifetimes(path) -> list[float]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT stored_at, expires_at FROM responses").fetchall()
    return [round(expires - stored) for stored, expires in rows]


class TestElevationLifetime:
    def test_an_elevation_response_is_stored_for_a_year(self, upstream, tmp_path):
        openmeteo.elevations([CALAMORRO])
        assert _lifetimes(tmp_path / "http.sqlite3") == [openmeteo.ELEVATION_TTL_S]
        assert openmeteo.ELEVATION_TTL_S == 365 * DAY

    def test_a_profile_fetched_the_day_before_is_served_from_the_cache(self, upstream, clock):
        path = [(36.63 + i / 1000, -4.50 - i / 1000) for i in range(40)]
        _, first_cached = openmeteo.elevations(path)
        clock.value += DAY
        values, cached = openmeteo.elevations(path)
        assert (first_cached, cached) == (False, True)
        assert values == [771.0] * 40
        assert upstream.paths == ["/v1/elevation"], "the second call reached the network"


class TestForecastLifetime:
    """The long lifetime must not leak to forecasts, which share the host."""

    def test_a_forecast_is_stored_for_ten_minutes(self, upstream, tmp_path):
        openmeteo.forecast(*CALAMORRO)
        assert _lifetimes(tmp_path / "http.sqlite3") == [600]

    def test_a_forecast_is_fetched_again_after_ten_minutes(self, upstream, clock):
        openmeteo.forecast(*CALAMORRO)
        clock.value += 9 * 60
        _, cached_at_nine = openmeteo.forecast(*CALAMORRO)
        clock.value += 2 * 60
        _, cached_at_eleven = openmeteo.forecast(*CALAMORRO)
        assert (cached_at_nine, cached_at_eleven) == (True, False)
        assert upstream.paths == ["/v1/forecast", "/v1/forecast"]
