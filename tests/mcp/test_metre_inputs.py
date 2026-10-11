"""A distance in degrees must not pass as a distance in metres.

A model that converts 7.5 km to 0.0674 degrees and sends it to a metres
parameter gets a 7 cm buffer, or a search that finds nothing and is reported as
an absence of data. Both look like answers. These tests pin the rejection, and
pin that real metre values -- including shrinking buffers -- still work.
"""

from __future__ import annotations

import json
import math

import pytest

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.tools._shared import MIN_METRES, require_metres

from ..conftest import mcp_client

POINT = '{"type":"Point","coordinates":[-4.4422,36.76479]}'
#: The 15 x 15 km square around Malaga used in the conference demo, 225 km^2.
SQUARE = (
    "POLYGON((-4.5262 36.69721, -4.3582 36.69721, -4.3582 36.83237, "
    "-4.5262 36.83237, -4.5262 36.69721))"
)


def _text(result) -> str:
    if result.structured_content:
        return json.dumps(result.structured_content)
    return "".join(getattr(block, "text", "") for block in (result.content or []))


class TestRequireMetres:
    @pytest.mark.parametrize("value", [0.0674, 0.0009, 0.999, -0.5])
    def test_sub_metre_values_are_rejected(self, value):
        with pytest.raises(GeoInputError, match="looks like a distance in degrees"):
            require_metres(value, name="distance_m", example="7500")

    @pytest.mark.parametrize("value", [MIN_METRES, 1.5, 7500.0, -100.0])
    def test_metre_values_pass_through(self, value):
        assert require_metres(value, name="distance_m", example="7500") == value

    def test_zero_is_left_to_the_caller(self):
        """Zero is an error for a buffer but a meaningful value elsewhere."""
        assert require_metres(0.0, name="distance_m", example="7500") == 0.0

    def test_message_names_the_parameter_the_value_and_a_fix(self):
        with pytest.raises(GeoInputError) as exc:
            require_metres(0.0674, name="radius_m", example="1000  (a 1 km search radius)")
        message = str(exc.value)
        assert "radius_m" in message
        assert "0.0674" in message
        assert "1000  (a 1 km search radius)" in message


class TestBuffer:
    async def test_a_buffer_in_degrees_is_an_error_not_a_tiny_polygon(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "geom_transform", {"geojson": POINT, "operation": "buffer", "distance_m": 0.0674}
            )
        assert result.is_error
        assert "degrees" in _text(result)

    async def test_a_buffer_in_metres_still_works(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "geom_transform", {"geojson": POINT, "operation": "buffer", "distance_m": 7500}
            )
        assert not result.is_error
        area = result.structured_content["measurements"]["area_km2"]
        assert area == pytest.approx(math.pi * 7.5**2, rel=0.005)

    async def test_a_shrinking_buffer_still_works(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "geom_transform", {"geojson": SQUARE, "operation": "buffer", "distance_m": -1000}
            )
        assert not result.is_error
        area = result.structured_content["measurements"]["area_km2"]
        assert area == pytest.approx(13.0 * 13.0, rel=0.02)  # 1 km off every side

    async def test_a_sub_metre_simplify_tolerance_is_allowed(self):
        """Simplifying fine survey data by 50 cm is legitimate, so it is not checked."""
        async with mcp_client() as client:
            result = await client.call_tool(
                "geom_transform", {"geojson": SQUARE, "operation": "simplify", "distance_m": 0.5}
            )
        assert not result.is_error


class TestSearchRadii:
    """The check runs before any request, so these stay offline.

    If it did not, the network guard in conftest would fail the call with a
    different message, and the "degrees" assertion would catch it.
    """

    async def test_rf_towers_rejects_a_radius_in_degrees(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "rf_towers", {"location": "36.6321,-4.5052", "radius_m": 0.05}
            )
        assert result.is_error
        assert "degrees" in _text(result)

    async def test_place_search_rejects_a_radius_in_degrees(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "place_search",
                {"location": "36.6321,-4.5052", "category": "fuel", "radius_m": 0.02},
            )
        assert result.is_error
        assert "degrees" in _text(result)


class TestClusterRadius:
    POINTS = "; ".join(f"36.72{i},-4.42{i}" for i in range(8))

    async def test_dbscan_rejects_eps_in_degrees(self):
        pytest.importorskip("sklearn", reason="the stats extra is not installed")
        async with mcp_client("analysis") as client:
            result = await client.call_tool(
                "stats_cluster", {"points": self.POINTS, "eps_m": 0.005, "algorithm": "dbscan"}
            )
        assert result.is_error
        assert "degrees" in _text(result)

    async def test_hdbscan_ignores_eps_so_does_not_check_it(self):
        pytest.importorskip("sklearn", reason="the stats extra is not installed")
        async with mcp_client("analysis") as client:
            result = await client.call_tool(
                "stats_cluster", {"points": self.POINTS, "eps_m": 0.005, "algorithm": "hdbscan"}
            )
        assert "degrees" not in _text(result)
