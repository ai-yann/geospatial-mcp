"""CRS transforms must say which operation ran and how good it is.

Regression tests. Before these, `ballpark` was read from an attribute pyproj
3.7 does not have, so it was always False -- including for Pulkovo 1942(83),
where PROJ ignores the datum shift entirely -- and `accuracy_m` was read from
the transformer before PROJ had chosen an operation, so it was always None.
"""

from __future__ import annotations

import pyproj.network
import pytest
from pyproj import Transformer

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.geo import crs
from geospatial_mcp.geo.crs import transform_point

#: Big Ben, on British National Grid and in WGS84.
BNG = (530000.0, 180000.0)

#: Changes of projection or axis order with no datum transformation, around
#: the Eiffel Tower. Every one is exact.
PROJECTION_CHANGES = [
    (2.2945, 48.8584, "EPSG:4326", "EPSG:3857"),
    (255422.0, 6250865.0, "EPSG:3857", "EPSG:4326"),
    (2.2945, 48.8584, "EPSG:4326", "EPSG:32631"),
    (448252.0, 5411935.0, "EPSG:32631", "EPSG:4326"),
    (448252.0, 5411935.0, "EPSG:32631", "EPSG:32632"),
    # A single-step conversion, which lists no steps of its own.
    (2.2945, 48.8584, "+proj=longlat +datum=WGS84 +no_defs", "+proj=merc +datum=WGS84 +no_defs"),
    # An axis-order change alone, and a projected CRS to itself. PROJ resolves
    # each to a no-op and records no last-used operation for the point.
    (2.2945, 48.8584, "EPSG:4326", "OGC:CRS84"),
    (2.2945, 48.8584, "OGC:CRS84", "EPSG:4326"),
    (255422.0, 6250865.0, "EPSG:3857", "EPSG:3857"),
    (448252.0, 5411935.0, "EPSG:32631", "EPSG:32631"),
]

#: The same CRS on both sides. PROJ routes EPSG:4326 to itself through a
#: "Null geographic offset", a transformation step, but the identity is exact.
SAME_CRS = [
    (2.2945, 48.8584, "EPSG:4326"),
    (*BNG, "EPSG:27700"),
]


class _NoPublishedAccuracy:
    """The operation PROJ used, with its accuracy reported as unknown (-1)."""

    accuracy = -1.0

    def __init__(self, operation: object) -> None:
        self._operation = operation

    def __getattr__(self, name: str) -> object:
        return getattr(self._operation, name)


def _hide_published_accuracy(monkeypatch: pytest.MonkeyPatch) -> None:
    real = crs._last_operation

    def last_operation(transformer, **kwargs):
        operation = real(transformer, **kwargs)
        return None if operation is None else _NoPublishedAccuracy(operation)

    monkeypatch.setattr(crs, "_last_operation", last_operation)


@pytest.fixture(autouse=True)
def _network_off(monkeypatch):
    """Tests are offline, and must not depend on a developer's PROJ_NETWORK."""
    was = pyproj.network.is_network_enabled()
    pyproj.network.set_network_enabled(False)
    yield
    pyproj.network.set_network_enabled(was)


class TestAccuracyReporting:
    def test_reports_the_operation_used_for_this_point(self):
        result = transform_point(*BNG, "EPSG:27700", "EPSG:4326")
        assert "OSGB36 to WGS 84" in result.pipeline
        assert result.accuracy_m == pytest.approx(2.0)
        assert not result.ballpark

    def test_names_the_grid_a_better_operation_needs(self):
        """OSTN15 gives 1 m where the Helmert fallback gives 2 m."""
        result = transform_point(*BNG, "EPSG:27700", "EPSG:4326")
        assert result.better_accuracy_m == pytest.approx(1.0)
        assert result.missing_grids == ["uk_os_OSTN15_NTv2_OSGBtoETRS.tif"]

    def test_only_grids_covering_the_point_are_suggested(self):
        """NAD27 has dozens of regional operations; Kansas must not be offered Canada's."""
        result = transform_point(-100.0, 40.0, "EPSG:4267", "EPSG:4326")
        assert result.missing_grids
        assert all(g.startswith("us_") for g in result.missing_grids), result.missing_grids

    @pytest.mark.parametrize(("x", "y", "source", "target"), PROJECTION_CHANGES)
    def test_a_pure_projection_change_is_exact_and_suggests_nothing(self, x, y, source, target):
        """No datum changes, so the result is exact, on PROJ 9.5 and 9.8 alike.

        PROJ 9.8 wraps these operations in an axis-order normalisation that
        publishes no accuracy (-1), where PROJ 9.5 published 0. Exact must not
        depend on which PROJ a fresh install happens to resolve.
        """
        result = transform_point(x, y, source, target)
        assert result.accuracy_m == 0.0
        assert result.missing_grids == []
        assert result.better_accuracy_m is None

    @pytest.mark.parametrize(("x", "y", "source", "target"), PROJECTION_CHANGES)
    def test_exact_does_not_rely_on_proj_publishing_an_accuracy(
        self, x, y, source, target, monkeypatch
    ):
        """Replays PROJ 9.8's unknown accuracy on whichever PROJ is installed."""
        _hide_published_accuracy(monkeypatch)
        assert transform_point(x, y, source, target).accuracy_m == 0.0

    def test_a_datum_shift_of_unknown_accuracy_is_not_called_exact(self, monkeypatch):
        """Only conversions are exact. A transformation step keeps "unknown"."""
        _hide_published_accuracy(monkeypatch)
        assert transform_point(*BNG, "EPSG:27700", "EPSG:4326").accuracy_m is None

    @pytest.mark.parametrize(("x", "y", "source", "target"), PROJECTION_CHANGES)
    def test_the_operation_for_a_projection_change_is_conversion_only(self, x, y, source, target):
        """Where PROJ records no last-used operation, the transformer stands in.

        Checked without the same-CRS shortcut, so EPSG:3857 to itself is
        classified from its steps like every other pair.
        """
        transformer = Transformer.from_crs(source, target, always_xy=True)
        transformer.transform(x, y)
        operation = crs._last_operation(transformer, transformed=True)
        assert operation is not None
        assert crs._is_conversion_only(operation)

    @pytest.mark.parametrize(
        ("source", "target"), [("EPSG:27700", "EPSG:4326"), ("EPSG:4178", "EPSG:4326")]
    )
    def test_a_transformer_still_choosing_an_operation_is_not_conversion_only(self, source, target):
        """The fallback must not turn a datum shift, or a ballpark offset, exact.

        Before a point goes through, a transformer with several candidate
        operations has no steps and no accuracy of its own.
        """
        transformer = Transformer.from_crs(source, target, always_xy=True)
        assert not crs._is_conversion_only(transformer)

    @pytest.mark.parametrize(("x", "y", "same"), SAME_CRS)
    def test_the_same_crs_on_both_sides_is_exact(self, x, y, same, monkeypatch):
        result = transform_point(x, y, same, same)
        assert (result.x, result.y) == pytest.approx((x, y))
        assert result.accuracy_m == 0.0
        _hide_published_accuracy(monkeypatch)
        assert transform_point(x, y, same, same).accuracy_m == 0.0


class TestBallpark:
    def test_a_transform_that_ignores_the_datum_shift_is_flagged(self):
        """Pulkovo 1942(83) has no published link to WGS84 in PROJ's database."""
        result = transform_point(20.0, 52.0, "EPSG:4178", "EPSG:4326")
        assert result.ballpark
        assert "ballpark" in result.pipeline.lower()
        # The ballpark offset is a transformation step, not a conversion: its
        # accuracy is unknown, and it must never be reported as exact.
        assert result.accuracy_m is None

    def test_a_published_transformation_is_not_flagged(self):
        result = transform_point(*BNG, "EPSG:27700", "EPSG:4326")
        assert not result.ballpark


class TestNetworkFallback:
    def test_an_unfetchable_grid_falls_back_rather_than_failing(self, tmp_path):
        """With PROJ_NETWORK on and no route to the grid CDN, PROJ returns inf.

        The honest answer is the 2 m local operation, not "outside the valid area".

        This runs in a subprocess, pointed at a closed local port. The socket
        guard in conftest cannot make the fetch fail: PROJ downloads grids
        through its own C libcurl, below Python's socket module, so in-process
        the test would either fetch the grid for real (on an open network, where
        it then fails for the wrong reason) or pass by luck (behind a proxy).
        A dead endpoint and an empty grid cache fail the same way everywhere,
        and send nothing off the machine.
        """
        import json
        import os
        import subprocess
        import sys

        script = (
            "import json\n"
            "from geospatial_mcp.geo.crs import transform_point\n"
            "r = transform_point(530000.0, 180000.0, 'EPSG:27700', 'EPSG:4326')\n"
            "print(json.dumps({'fallback': r.network_fallback, 'accuracy': r.accuracy_m,"
            " 'grids': r.missing_grids, 'lat': r.y}))\n"
        )
        env = {
            **os.environ,
            "PROJ_NETWORK": "ON",
            "PROJ_NETWORK_ENDPOINT": "http://127.0.0.1:9",
            "PROJ_USER_WRITABLE_DIRECTORY": str(tmp_path),
        }
        run = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120
        )
        assert run.returncode == 0, run.stderr
        result = json.loads(run.stdout.strip().splitlines()[-1])
        assert result["fallback"]
        assert result["accuracy"] == pytest.approx(2.0)
        assert "uk_os_OSTN15_NTv2_OSGBtoETRS.tif" in result["grids"]
        assert result["lat"] == pytest.approx(51.50399, abs=1e-4)

    def test_a_point_genuinely_outside_the_crs_still_raises(self):
        with pytest.raises(GeoInputError):
            transform_point(1e12, 1e12, "EPSG:27700", "EPSG:4326")
