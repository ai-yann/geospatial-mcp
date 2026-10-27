"""The public tool contract for terrain clearance and fixed-distance power."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from geospatial_mcp.geo import geodesy
from geospatial_mcp.net.providers import openmeteo

from ..conftest import mcp_client

ORIGIN = "36.6320866,-4.5051825"
DESTINATION = "36.6168069,-4.5610885"
PATH = {"origin": ORIGIN, "destination": DESTINATION}
HEIGHTS = {"origin_antenna_height_m": 10, "destination_antenna_height_m": 10}


async def _call(tool, arguments):
    async with mcp_client() as client:
        result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def flat_surface(monkeypatch):
    requests = []

    def elevations(points):
        requests.append(points)
        return [100.0] * len(points), True

    monkeypatch.setattr(openmeteo, "elevations", elevations)
    return requests


class TestProfileCompatibility:
    async def test_without_heights_the_original_payload_is_preserved(self, flat_surface):
        result = await _call("terrain_profile", {**PATH, "samples": 3})
        assert set(result) == {"total_distance_m", "samples", "statistics", "profile", "meta"}
        assert result["samples"] == 3
        assert result["statistics"]["relief_m"] == 0
        assert result["statistics"]["gaps"] == 0
        assert result["meta"]["notes"] == []
        expected = geodesy.densify(36.6320866, -4.5051825, 36.6168069, -4.5610885, 3)
        assert result["profile"] == [
            {
                "distance_m": round(d, 1),
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "elevation_m": 100.0,
            }
            for lat, lon, d in expected
        ]

    async def test_coordinates_can_be_omitted_without_losing_chart_values(self, flat_surface):
        full = await _call("terrain_profile", {**PATH, "samples": 50})
        compact = await _call(
            "terrain_profile", {**PATH, "samples": 50, "include_coordinates": False}
        )
        assert compact["statistics"] == full["statistics"]
        assert compact["profile"] == [
            {k: v for k, v in p.items() if k in {"distance_m", "elevation_m"}}
            for p in full["profile"]
        ]
        assert len(json.dumps(compact)) < 0.7 * len(json.dumps(full))


class TestRadioProfile:
    async def test_radio_sampling_is_independent_of_requested_samples(self, flat_surface):
        low = await _call(
            "terrain_profile", {**PATH, **HEIGHTS, "samples": 2, "frequency_mhz": 5800}
        )
        high = await _call(
            "terrain_profile", {**PATH, **HEIGHTS, "samples": 100, "frequency_mhz": 5800}
        )
        assert low == high
        sightline = low["line_of_sight"]
        assert low["samples"] == math.ceil(low["total_distance_m"] / 90) + 1
        assert sightline["sample_spacing_m"] <= sightline["grid_spacing_m"] == 90
        assert sightline["clear"] is True
        assert sightline["fresnel_60_percent_clear"] is True
        assert sightline["required_fresnel_clearance_fraction"] == 0.6
        assert len(sightline["clearance_m"]) == low["samples"]
        notes = " ".join(low["meta"]["notes"])
        assert "surface model" in notes and "canopy" in notes
        assert "<4 m" in notes and "90%" in notes and "No diffraction loss" in notes

    async def test_custom_k_factor_changes_the_sightline(self, flat_surface):
        normal = await _call("terrain_profile", {**PATH, **HEIGHTS})
        unrefracted = await _call("terrain_profile", {**PATH, **HEIGHTS, "k_factor": 1})
        assert unrefracted["line_of_sight"]["k_factor"] == 1
        assert (
            unrefracted["line_of_sight"]["worst_point"]["clearance_m"]
            < normal["line_of_sight"]["worst_point"]["clearance_m"]
        )

    async def test_long_paths_keep_grid_spacing_past_100_samples(self, flat_surface):
        result = await _call(
            "terrain_profile",
            {
                "origin": "36.6,-4.5",
                "destination": "36.6,-4.7",
                **HEIGHTS,
                "samples": 2,
            },
        )
        assert result["samples"] > 100
        assert len(flat_surface[0]) == result["samples"]
        assert result["line_of_sight"]["sample_spacing_m"] <= 90

    @pytest.mark.parametrize(
        "parameters",
        [
            {"origin_antenna_height_m": 10},
            {"frequency_mhz": 5800},
            {**HEIGHTS, "origin": ORIGIN, "destination": ORIGIN},
        ],
    )
    async def test_invalid_combinations_teach_recovery(self, parameters):
        async with mcp_client() as client:
            result = await client.call_tool("terrain_profile", {**PATH, **parameters})
        assert result.is_error
        message = " ".join(getattr(block, "text", "") for block in result.content)
        assert "Example of a valid value:" in message

    async def test_missing_endpoint_returns_unknown_instead_of_clear(self, monkeypatch):
        monkeypatch.setattr(
            openmeteo, "elevations", lambda pts: ([None] + [100] * (len(pts) - 1), False)
        )
        result = await _call("terrain_profile", {**PATH, **HEIGHTS, "frequency_mhz": 5800})
        assert result["line_of_sight"]["clear"] is None
        assert result["line_of_sight"]["fresnel_60_percent_clear"] is None
        assert result["profile"][0]["elevation_m"] is None


class TestPowerContract:
    async def test_default_cell_budget_keeps_the_four_towers_numbers(self):
        result = await _call("rf_link_budget", {"environment": "suburban"})
        assert result["model"] == "cost231_hata"
        assert result["cell_radius_km"] == 3.2055
        assert result["shadow_margin_db"] == 9
        assert "distance_km" not in result and "closes_on_power" not in result

    async def test_fixed_distance_margin_is_after_the_separate_shadow_margin(self):
        result = await _call(
            "rf_link_budget",
            {
                "frequency_mhz": 5800,
                "model": "free_space",
                "distance_km": 5.28,
            },
        )
        assert result["path_loss_db"] == pytest.approx(122.16, abs=0.02)
        assert result["received_power_dbm"] == pytest.approx(-67.16, abs=0.02)
        assert result["power_margin_db"] == pytest.approx(37.84, abs=0.02)
        assert result["fade_margin_db"] == pytest.approx(24.84, abs=0.02)
        assert result["shadow_margin_db"] == 13
        assert result["closes_on_power"] is True
        assert "No terrain" in " ".join(result["meta"]["notes"])

    async def test_invalid_empirical_frequency_is_an_mcp_error(self):
        async with mcp_client() as client:
            result = await client.call_tool("rf_link_budget", {"frequency_mhz": 5800})
        assert result.is_error
        message = " ".join(getattr(block, "text", "") for block in result.content)
        assert 'model="free_space"' in message and "distance_km" in message
        assert "terrain_profile" in message


async def test_recorded_demo_path_is_blocked_near_5_2_km_but_closes_on_power(monkeypatch):
    """Reuse t17's real GLO-90 values, with nearest-sample lookup for the new grid.

    This fixture checks the recorded obstruction. It makes no claim to recover
    new DEM values at positions between those recorded samples.
    """
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "torremolinos_calamorro_profile.json").read_text()
    )
    origin = tuple(map(float, fixture["origin"].split(",")))
    recorded = fixture["distance_km"]

    def elevations(points):
        values = []
        for point in points:
            distance_km = geodesy.distance_m(*origin, *point) / 1000
            i = min(range(len(recorded)), key=lambda i: abs(recorded[i] - distance_km))
            values.append(fixture["ground_m"][i])
        return values, True

    monkeypatch.setattr(openmeteo, "elevations", elevations)
    profile = await _call(
        "terrain_profile",
        {
            **PATH,
            **HEIGHTS,
            "frequency_mhz": 5800,
            "include_coordinates": False,
        },
    )
    sightline = profile["line_of_sight"]
    assert sightline["clear"] is False and sightline["fresnel_60_percent_clear"] is False
    assert sightline["worst_point"]["distance_m"] == pytest.approx(5200, abs=80)
    assert sightline["worst_point"]["clearance_m"] < -5
    power = await _call(
        "rf_link_budget",
        {
            "frequency_mhz": 5800,
            "model": "free_space",
            "distance_km": profile["total_distance_m"] / 1000,
            "tx_height_m": 10,
            "rx_height_m": 10,
        },
    )
    assert power["closes_on_power"] is True and power["fade_margin_db"] > 20


async def test_grid_spaced_demo_finds_the_741_m_ridge(monkeypatch):
    """Real 27 October samples catch a cell the old 50-point take missed."""
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "torremolinos_calamorro_profile.json").read_text()
    )

    def elevations(points):
        if len(points) == 50:
            return fixture["ground_m"], True
        assert len(points) == len(fixture["grid_90m_ground_m"]) == 60
        return fixture["grid_90m_ground_m"], True

    monkeypatch.setattr(openmeteo, "elevations", elevations)
    old = await _call("terrain_profile", PATH)
    radio = await _call("terrain_profile", {**PATH, **HEIGHTS, "frequency_mhz": 5800})
    assert old["statistics"]["max_m"] == 725
    worst = radio["line_of_sight"]["worst_point"]
    assert radio["line_of_sight"]["clear"] is False
    assert worst["ground_elevation_m"] == 741
    assert worst["distance_m"] == pytest.approx(5190.805, abs=0.01)
    assert worst["clearance_m"] == pytest.approx(-26.688, abs=0.01)
    assert worst["first_fresnel_radius_m"] == pytest.approx(2.133, abs=0.01)
