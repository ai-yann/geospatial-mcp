"""The deterministic answer and artifact checks the eval runners score with."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from answers import check_answer, check_artifacts, numbers_in


class TestNumbers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("covers 57.4% of the square", [57.4]),
            ("a 7,500 m buffer", [7500.0]),
            ("36.632, -4.505", [36.632, -4.505]),
            ("129 of 225 km2", [129.0, 225.0, 2.0]),
        ],
    )
    def test_numbers_are_read_as_written(self, text, expected):
        assert numbers_in(text) == expected


class TestCheckAnswer:
    def test_no_spec_means_unscored(self):
        assert check_answer(None, "anything") == (None, "")

    def test_a_number_within_tolerance_passes(self):
        ok, _ = check_answer({"numbers": [{"near": 57.4, "tol": 3}]}, "They cover about 57%.")
        assert ok

    def test_the_hexagon_mistake_fails_the_circle_check(self):
        ok, detail = check_answer({"numbers": [{"near": 129.1, "tol": 3}]}, "About 106.8 km2.")
        assert not ok
        assert "129.1" in detail

    def test_patterns_are_case_insensitive_and_all_required(self):
        spec = {"patterns": ["palacio de congresos", "36\\.63"]}
        assert check_answer(spec, "The Palacio de Congresos, at 36.632 N")[0]
        assert not check_answer(spec, "The Palacio de Congresos")[0]

    def test_a_forbidden_claim_fails(self):
        spec = {"forbid": ["there are no (cell |mobile |phone )*(towers|masts)"]}
        ok, detail = check_answer(spec, "There are no mobile phone masts nearby.")
        assert not ok
        assert "says" in detail


def _circle(path: Path, lon_scale: float) -> None:
    """A 7.5 km 'circle' at Malaga cathedral; lon_scale < 1 squashes it like a degree buffer."""
    import math

    lat, lon = 36.7201788, -4.4194052
    r_lat = 7.5 / 111.0
    r_lon = r_lat / math.cos(math.radians(lat)) * lon_scale
    ring = [
        [
            lon + r_lon * math.cos(2 * math.pi * i / 128),
            lat + r_lat * math.sin(2 * math.pi * i / 128),
        ]
        for i in range(129)
    ]
    path.write_text(json.dumps({"type": "Polygon", "coordinates": [ring]}), encoding="utf-8")


class TestArtifacts:
    SPEC = [
        {
            "file": "zone.geojson",
            "area_km2": {"near": 176.7, "tol": 3},
            "aspect": {"near": 1.0, "tol": 0.05},
        }
    ]

    def test_a_true_circle_passes(self, tmp_path):
        _circle(tmp_path / "zone.geojson", lon_scale=1.0)
        ok, detail = check_artifacts(self.SPEC, tmp_path)
        assert ok, detail

    def test_a_degree_space_ellipse_fails_on_area_and_shape(self, tmp_path):
        import math

        _circle(tmp_path / "zone.geojson", lon_scale=math.cos(math.radians(36.72)))
        ok, detail = check_artifacts(self.SPEC, tmp_path)
        assert not ok
        assert "area" in detail
        assert "east-west" in detail

    def test_a_missing_file_fails(self, tmp_path):
        ok, detail = check_artifacts(self.SPEC, tmp_path)
        assert not ok
        assert "not written" in detail

    def test_a_feature_collection_is_accepted(self, tmp_path):
        _circle(tmp_path / "raw.json", lon_scale=1.0)
        geometry = json.loads((tmp_path / "raw.json").read_text())
        collection = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
        }
        (tmp_path / "zone.geojson").write_text(json.dumps(collection))
        assert check_artifacts(self.SPEC, tmp_path)[0]
