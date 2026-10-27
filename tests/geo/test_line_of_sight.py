"""Radio clearance checked against ITU-R reference equations and edge cases."""

from __future__ import annotations

import pytest

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.geo.line_of_sight import (
    earth_curvature_bulge_m,
    first_fresnel_radius_m,
    path_clearance,
)


class TestReferenceValues:
    def test_effective_earth_midpoint_bulge(self):
        """P.526 effective radius: a 50 km path, k=4/3, midpoint bulge 36.8 m."""
        assert earth_curvature_bulge_m(25, 25) == pytest.approx(36.795, abs=0.01)

    def test_unrefracted_midpoint_bulge(self):
        """The same reference path with k=1 has a 49.06 m bulge."""
        assert earth_curvature_bulge_m(25, 25, 1) == pytest.approx(49.058, abs=0.01)

    def test_first_fresnel_radius_reference(self):
        """P.530 (3): a 40 km, 6 GHz path has a midpoint radius of 22.3 m."""
        assert first_fresnel_radius_m(20, 20, 6000) == pytest.approx(22.36, abs=0.04)

    def test_short_microwave_reference(self):
        """P.526 (2): a 5 km, 5 GHz path has an 8.66 m midpoint radius."""
        assert first_fresnel_radius_m(2.5, 2.5, 5000) == pytest.approx(8.66, abs=0.01)

    def test_terminal_values_are_zero(self):
        assert earth_curvature_bulge_m(0, 5) == 0
        assert first_fresnel_radius_m(5, 0, 5800) == 0

    @pytest.mark.parametrize("k", [0, -1, float("inf"), float("nan")])
    def test_invalid_refraction_is_a_recoverable_input_error(self, k):
        with pytest.raises(GeoInputError, match="k_factor"):
            earth_curvature_bulge_m(1, 1, k)

    @pytest.mark.parametrize("frequency", [0, -1, float("nan")])
    def test_invalid_frequency_is_a_recoverable_input_error(self, frequency):
        with pytest.raises(GeoInputError, match="frequency_mhz"):
            first_fresnel_radius_m(1, 1, frequency)


def _path(elevations, *, heights=10, frequency=5800):
    return path_clearance(
        [0, 2500, 5000],
        elevations,
        origin_antenna_height_m=heights,
        destination_antenna_height_m=heights,
        frequency_mhz=frequency,
    )


class TestClearance:
    def test_curvature_can_block_a_flat_path(self):
        result = _path([100, 100, 100], heights=0.3)
        assert result.clear is False
        assert result.worst_point is not None
        assert result.worst_point.clearance_m == pytest.approx(-0.068, abs=0.001)
        assert result.worst_point.earth_bulge_m == pytest.approx(0.368, abs=0.001)

    def test_a_visible_path_can_fail_the_fresnel_rule(self):
        result = _path([100, 100, 100], heights=3)
        assert result.clear is True
        assert result.fresnel_60_percent_clear is False
        assert result.worst_point is not None
        assert result.worst_point.fresnel_clearance_fraction == pytest.approx(0.327, abs=0.002)

    def test_clearance_above_60_percent_passes(self):
        assert _path([100, 100, 100], heights=6).fresnel_60_percent_clear is True

    def test_fresnel_limiting_point_can_differ_from_geometric_worst_point(self):
        result = path_clearance(
            [0, 100, 2500, 5000],
            [100, 108, 103, 100],
            origin_antenna_height_m=10,
            destination_antenna_height_m=10,
            frequency_mhz=5800,
        )
        assert result.worst_point is not None and result.worst_fresnel_point is not None
        assert result.worst_point.distance_m == 100
        assert result.worst_fresnel_point.distance_m == 2500

    def test_an_endpoint_gap_prevents_any_verdict(self):
        result = _path([None, 200, 100])
        assert result.clear is None and result.worst_point is None
        assert result.sightline_height_m == [None, None, None]

    def test_an_interior_gap_cannot_prove_clearance(self):
        result = _path([100, None, 100])
        assert result.clear is None
        assert result.fresnel_60_percent_clear is None
        assert result.clearance_m[1] is None

    def test_a_known_obstruction_survives_another_gap(self):
        result = path_clearance(
            [0, 1500, 3000, 5000],
            [100, None, 200, 100],
            origin_antenna_height_m=10,
            destination_antenna_height_m=10,
            frequency_mhz=5800,
        )
        assert result.clear is False and result.fresnel_60_percent_clear is False

    def test_no_frequency_still_computes_geometric_clearance(self):
        result = _path([100, 100, 100], frequency=None)
        assert result.clear is True
        assert result.worst_point is not None
        assert result.worst_point.first_fresnel_radius_m is None

    def test_duplicate_endpoints_are_rejected(self):
        with pytest.raises(GeoInputError, match="increase"):
            path_clearance(
                [0, 0],
                [100, 100],
                origin_antenna_height_m=10,
                destination_antenna_height_m=10,
            )
