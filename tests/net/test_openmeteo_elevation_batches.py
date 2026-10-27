"""Grid-spaced radio profiles must survive the provider's 100-point limit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geospatial_mcp.errors import GeoUpstreamError
from geospatial_mcp.net.providers import openmeteo


@pytest.mark.parametrize(
    "cache_flags, all_cached", [([True, True, True], True), ([True, False, True], False)]
)
def test_batches_keep_order_gaps_and_cache_status(monkeypatch, cache_flags, all_cached):
    batches = []

    def get_json(url, *, params, **kwargs):
        values = [float(v) for v in params["latitude"].split(",")]
        batches.append(values)
        return {"elevation": [None if v == 1.5 else v for v in values]}, cache_flags.pop(0)

    monkeypatch.setattr(openmeteo, "get_client", lambda: SimpleNamespace(get_json=get_json))
    points = [(i / 100, 0.0) for i in range(205)]
    values, cached = openmeteo.elevations(points)
    assert [len(b) for b in batches] == [100, 100, 5]
    assert values == [None if lat == 1.5 else lat for lat, _ in points]
    assert cached is all_cached


def test_a_short_response_cannot_shift_elevations_onto_other_positions(monkeypatch):
    monkeypatch.setattr(
        openmeteo,
        "get_client",
        lambda: SimpleNamespace(get_json=lambda *args, **kwargs: ({"elevation": [100]}, False)),
    )
    with pytest.raises(GeoUpstreamError, match="Cannot align samples"):
        openmeteo.elevations([(36.6, -4.5), (36.6, -4.6)])


def test_an_empty_profile_makes_no_request(monkeypatch):
    def unexpected_request():
        raise AssertionError("An empty profile reached the provider")

    monkeypatch.setattr(openmeteo, "get_client", unexpected_request)
    assert openmeteo.elevations([]) == ([], False)
