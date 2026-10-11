"""CRS transforms must say which operation ran and how good it is.

Regression tests. Before these, `ballpark` was read from an attribute pyproj
3.7 does not have, so it was always False -- including for Pulkovo 1942(83),
where PROJ ignores the datum shift entirely -- and `accuracy_m` was read from
the transformer before PROJ had chosen an operation, so it was always None.
"""

from __future__ import annotations

import pyproj.network
import pytest

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.geo.crs import transform_point

#: Big Ben, on British National Grid and in WGS84.
BNG = (530000.0, 180000.0)


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

    def test_a_pure_projection_change_is_exact_and_suggests_nothing(self):
        result = transform_point(2.2945, 48.8584, "EPSG:4326", "EPSG:3857")
        assert result.accuracy_m == 0.0
        assert result.missing_grids == []
        assert result.better_accuracy_m is None


class TestBallpark:
    def test_a_transform_that_ignores_the_datum_shift_is_flagged(self):
        """Pulkovo 1942(83) has no published link to WGS84 in PROJ's database."""
        result = transform_point(20.0, 52.0, "EPSG:4178", "EPSG:4326")
        assert result.ballpark
        assert "ballpark" in result.pipeline.lower()

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
