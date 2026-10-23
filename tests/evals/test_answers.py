"""The deterministic answer and artifact checks the eval runners score with."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from answers import UNITS, check_answer, check_artifacts, headline, numbers_in

EVALS = Path(__file__).resolve().parents[2] / "evals"
LIBRARY = {
    case["id"]: case for case in yaml.safe_load((EVALS / "prompts.yaml").read_text(encoding="utf-8"))["cases"]
}

#: Real answers to telecom-cell-area-sum-007 (Qwen3.8-27B in OpenCode, 17 Oct 2026). The first
#: leads with the hexagonal area and gives the circle's 128.7 in an aside; it passed the old check.
#: The second leads with 129 and gives the hexagons' 107 in an aside.
HEXAGONS_FIRST = """

**≈ 107 km²**

Cost 231 Hata (1800 MHz, suburban, 9 dB default shadow margin) gives a max allowable path loss of 160 dB → cell radius **3.21 km** → 26.70 km² per cell (hexagonal serving area). With no overlap between the four cells:

4 × 26.70 km² ≈ **106.8 km²**

(If you instead count each cell's full circular footprint, π r² = 32.2 km² each → ≈ 128.7 km².)
"""
CIRCLES_FIRST = """

Radius per cell: 3.21 km (Cost231-Hata, 9 dB shadow margin, 151 dB max path loss). Since the cells are isolated and non-overlapping, each covers a circular footprint:

- π × 3.2055² ≈ 32.3 km² per cell
- × 4 ≈ **129 km² total**

(If you were tiling them as a planned hexagonal network instead, it would be 4 × 26.7 ≈ 107 km².)
"""
#: Sonnet 5 in Claude Code, September 2026: the per-cell area in bold before the total.
PER_CELL_FIRST = """Each cell has a link-budget-limited radius of **3.21 km** (COST-231 Hata, suburban).

- Area per cell = π × 3.2055² ≈ **32.3 km²**
- Total for 4 cells = 4 × 32.3 ≈ **129 km²**

(Note: the tool's `cell_area_km2` field of 26.7 km² is the *hexagonal* tiling area.)"""


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


class TestHeadline:
    """A number is checked against the figure the answer settles on, not any number in it."""

    CELL_AREA = LIBRARY["telecom-cell-area-sum-007"]["answer"]

    def test_hexagons_first_with_the_circle_in_an_aside_fails(self):
        ok, detail = check_answer(self.CELL_AREA, HEXAGONS_FIRST)
        assert not ok
        # Not the bold 107 on the first line: nothing there says it is the total.
        assert detail == "headline 106.8 km2, expected 129.1±3"

    def test_the_old_check_passed_it(self):
        assert any(abs(n - 129.1) <= 3 for n in numbers_in(HEXAGONS_FIRST))

    def test_headline_106_8_with_129_in_an_aside_fails(self):
        text = "Four cells cover 4 × 26.70 ≈ **106.8 km²** in total.\n(As circles: 129 km².)"
        ok, detail = check_answer(self.CELL_AREA, text)
        assert not ok
        assert "headline 106.8 km2" in detail

    def test_circles_first_with_the_hexagons_in_an_aside_passes(self):
        assert check_answer(self.CELL_AREA, CIRCLES_FIRST) == (True, "")

    def test_the_total_is_read_past_the_per_cell_area_in_bold(self):
        assert headline(PER_CELL_FIRST, self.CELL_AREA["numbers"][0]) == 129.0
        assert check_answer(self.CELL_AREA, PER_CELL_FIRST)[0]

    def test_a_per_cell_area_beside_the_total_does_not_count_as_the_total(self):
        text = "A circle per cell gives about **32.3 km² each**, or **129 km² total**."
        assert headline(text, self.CELL_AREA["numbers"][0]) == 129.0

    def test_the_first_bold_figure_wins_over_a_later_one(self):
        text = "About **107 km²** in total. As circles, the total would be **129 km²**."
        assert headline(text, {"unit": "km2"}) == 107.0

    def test_without_bold_the_first_figure_counts(self):
        text = "The four cells cover 129 km² in total; tiled hexagons would give 107 km²."
        assert headline(text, {"unit": "km2"}) == 129.0

    def test_a_figure_in_another_unit_is_not_the_headline(self):
        """330 ft is within 150 of 306, but it is a height above the river in feet."""
        spec = LIBRARY["consumer-trip-conditions-004"]["answer"]
        text = "**Height:** the walkway hangs about **100 m (330 ft)** above the river."
        ok, detail = check_answer(spec, text)
        assert not ok
        assert detail.startswith("no figure in m near /")

    def test_about_picks_the_elevation_over_the_bold_height_above_the_river(self):
        spec = LIBRARY["consumer-trip-conditions-004"]["answer"]
        text = (
            "**Height:** The trailhead is ~306 m above sea level, but the walkway itself "
            "is roughly **100 m above the Guadalhorce river**."
        )
        assert headline(text, spec["numbers"][0]) == 306.0
        assert check_answer(spec, text)[0]

    def test_a_wrong_requirement_stated_first_fails(self):
        spec = LIBRARY["telecom-four-site-fix-006"]["answer"]
        text = (
            "With four sites at the **centres of the four 7.5 × 7.5 km quadrants**, each must "
            "reach about **10.6 km**. The 1800 MHz estimate gives **5.26 km**, so use 800 MHz."
        )
        ok, detail = check_answer(spec, text)
        assert not ok
        assert detail == "headline 10.6 km, expected 5.3±0.1"

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("r = **7.5 km / √2 ≈ 5.30 km**", 5.30),  # the divided figure is working
            ("**r_required = 7.5 km × √2 ÷ 2 ≈ 5.30 km**", 5.30),
            ("one site per **7.5 × 7.5 km quadrant**, 5.3 km to the corner", 5.3),
            ("4 × 32.3 km² = **129.2 km²**", 129.2),
        ],
    )
    def test_the_operands_of_a_sum_are_not_headlines(self, text, value):
        unit = "km2" if "km²" in text else "km"
        assert headline(text, {"unit": unit}) == value

    @pytest.mark.parametrize(
        ("text", "unit", "value"),
        [
            ("wind 21 km/h, radius 3.21 km", "km", 3.21),
            ("32.3 km² per cell, radius 3.21 km", "km", 3.21),
            ("a 90 m-resolution model; at 1800 MHz; 2 mm of rain; 342 m above sea level", "m", 342),
            ("about 57.4 per cent of the square", "%", 57.4),
            ("1,273 ft or 388 metres", "m", 388),
            ("17,664 hectares, or 176.64 square kilometres", "km2", 176.64),
        ],
    )
    def test_units_are_read_as_written(self, text, unit, value):
        assert headline(text, {"unit": unit}) == value

    def test_a_bold_figure_written_in_latex_counts(self):
        """Codex sometimes answers in LaTeX."""
        text = r"r=\sqrt{7.5^2+7.5^2}\approx\mathbf{10.6\ km}, while the link reaches **5.3 km**"
        assert headline(text, {"unit": "km"}) == 10.6

    def test_an_answer_with_no_figure_fails_and_says_so(self):
        ok, detail = check_answer({"numbers": [{"near": 2.23, "tol": 0.2, "unit": "km"}]}, "Far.")
        assert not ok
        assert detail == "no figure in km, expected 2.23±0.2"

    def test_an_unknown_unit_is_refused(self):
        with pytest.raises(ValueError, match="unknown unit"):
            headline("5 furlongs", {"unit": "furlong"})


class TestLibrary:
    def test_every_number_names_a_known_unit(self):
        """Without a unit, the headline is the first number of any kind in the answer."""
        for case in LIBRARY.values():
            for want in (case.get("answer") or {}).get("numbers", []):
                assert want.get("unit") in UNITS, case["id"]


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
