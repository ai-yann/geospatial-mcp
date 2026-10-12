"""Polymorphic location parsing and USNG/MGRS grid references."""

from __future__ import annotations

import pytest

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.geo import geodesy, grid_ref
from geospatial_mcp.geo.coords import format_dms, looks_like_place_name, parse_location

#: The Eiffel Tower, expressed every way this parser accepts.
EIFFEL = 48.8584, 2.2945


class TestPolymorphicParsing:
    @pytest.mark.parametrize(
        ("text", "kind", "tolerance_m"),
        [
            ("48.8584, 2.2945", "latlon", 0.1),
            ("48.8584 2.2945", "latlon", 0.1),
            ("48.8584,2.2945", "latlon", 0.1),
            ("48°51'30\"N 2°17'40\"E", "dms", 30.0),
            ("31UDQ4825211954", "grid", 2.0),
            ("u09tunquc", "geohash", 5.0),
            ("geohash:u09tunquc", "geohash", 5.0),
            ("891fb46741bffff", "h3", 200.0),
            ("8FW4V75V+9R6", "pluscode", 5.0),
        ],
    )
    def test_every_notation_resolves_to_the_same_place(self, text, kind, tolerance_m):
        parsed = parse_location(text)
        assert parsed.kind == kind
        assert parsed.resolved
        error = geodesy.distance_m(*EIFFEL, *parsed.as_tuple())
        assert error <= tolerance_m, f"{text} landed {error:.1f} m away"

    @pytest.mark.parametrize("text", ["Eiffel Tower, Paris", "Berlin", "10 Downing Street"])
    def test_free_text_is_deferred_to_a_geocoder(self, text):
        parsed = parse_location(text)
        assert parsed.kind == "place_name"
        assert not parsed.resolved

    def test_unresolved_location_explains_itself(self):
        parsed = parse_location("Berlin")
        with pytest.raises(GeoInputError, match="geocode"):
            parsed.as_tuple()

    def test_southern_and_western_hemispheres(self):
        parsed = parse_location("-33.8688,151.2093")
        assert parsed.as_tuple() == pytest.approx((-33.8688, 151.2093))

    def test_hemisphere_letters_override_ordering(self):
        """'2E 48N' names the same place as '48N 2E'."""
        a = parse_location("48°51'30\"N 2°17'40\"E").as_tuple()
        b = parse_location("2°17'40\"E 48°51'30\"N").as_tuple()
        assert a == pytest.approx(b)


class TestGeohashOrPlaceName:
    """A word spelt from the geohash alphabet is a place name first.

    Regression tests. Any 4+ character token made only of geohash characters
    used to be decoded with no note, so "Denver" landed in the Atlantic at
    17.88, -57.86, and "Bergen" and "Berne" in Alaska.
    """

    @pytest.mark.parametrize(
        "text",
        ["Denver", "Bergen", "Berne", "Sydney", "Perth", "bergen", "denver", "SW19", "90210"],
    )
    def test_a_word_that_is_also_a_geohash_is_a_place_name_first(self, text):
        parsed = parse_location(text)
        assert parsed.kind == "place_name"
        assert not parsed.resolved, "must be geocoded, not decoded"
        assert parsed.ambiguous_geohash
        assert looks_like_place_name(text)

    @pytest.mark.parametrize("text", ["Málaga", "Malaga", "Berlin", "NYC"])
    def test_a_name_outside_the_alphabet_is_not_flagged(self, text):
        parsed = parse_location(text)
        assert parsed.kind == "place_name"
        assert not parsed.ambiguous_geohash

    @pytest.mark.parametrize(
        ("text", "lat", "lon"),
        [
            ("u09tunquc", 48.8584, 2.2945),  # the Eiffel Tower, as documented
            ("eysb3kxmm", 36.6321, -4.5052),  # Málaga
            ("ezs42", 42.605, -5.603),  # the worked example on Wikipedia's Geohash page
            ("dr5ru", 40.759, -73.982),  # Manhattan
        ],
    )
    def test_a_lowercase_geohash_with_a_digit_is_decoded_at_once(self, text, lat, lon):
        parsed = parse_location(text)
        assert parsed.kind == "geohash"
        assert not parsed.ambiguous_geohash
        assert parsed.as_tuple() == pytest.approx((lat, lon), abs=0.01)
        assert not looks_like_place_name(text)

    @pytest.mark.parametrize(
        ("text", "lat", "lon"),
        [
            ("geohash:gcpvj", 51.526, -0.110),  # London, no digit
            ("GEOHASH: GCPVJ", 51.526, -0.110),
            ("geohash:u33d", 52.471, 13.535),  # Berlin
            ("geohash:s000", 0.088, 0.176),  # has a bare 's', which could look like DMS
            ("geohash:bergen", 63.932, -146.464),  # marked, so the code wins over the city
        ],
    )
    def test_a_marked_geohash_is_decoded_whatever_it_spells(self, text, lat, lon):
        parsed = parse_location(text)
        assert parsed.kind == "geohash"
        assert parsed.raw == text.strip()
        assert parsed.as_tuple() == pytest.approx((lat, lon), abs=0.01)
        assert not looks_like_place_name(text)

    def test_a_marked_geohash_keeps_its_precision(self):
        assert parse_location("geohash:gcpvj").precision_m == 4890.0

    @pytest.mark.parametrize("text", ["geohash:hello", "geohash:", "geohash:u09tunqucu09t"])
    def test_a_bad_marked_geohash_names_the_alphabet(self, text):
        with pytest.raises(GeoInputError) as exc:
            parse_location(text)
        message = str(exc.value)
        assert "a, i, l and o" in message
        assert "geohash:u09tunquc" in message, "the message must show a valid example"


class TestRangeValidation:
    @pytest.mark.parametrize("text", ["999, 0", "91, 0", "-91, 0"])
    def test_latitude_out_of_range(self, text):
        with pytest.raises(GeoInputError, match="Latitude must be between"):
            parse_location(text)

    @pytest.mark.parametrize("text", ["0, 999", "0, 181", "0, -181"])
    def test_longitude_out_of_range(self, text):
        with pytest.raises(GeoInputError, match="Longitude must be between"):
            parse_location(text)

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_input(self, text):
        with pytest.raises(GeoInputError):
            parse_location(text)


class TestGridReferences:
    """USNG and MGRS are the same grid; the code calls it a grid reference.

    The golden vectors below were cross-checked against NGA GEOTRANS (via the
    `mgrs` package) before that dependency was dropped in favour of pygeodesy,
    which is pure Python and already needed for rhumb lines. All nine agree to
    the last digit, polar UPS zones included, as do 2999 of 3000 random global
    positions at every precision -- the one exception differs by a metre in the
    last digit, where the two projections disagree by under a millimetre.
    """

    @pytest.mark.parametrize(
        ("digits", "expected_square_m"),
        [(2, 10_000), (4, 1_000), (6, 100), (8, 10), (10, 1)],
    )
    def test_precision_degrades_predictably(self, digits, expected_square_m):
        reference = grid_ref.from_latlon(*EIFFEL, digits)
        assert grid_ref.precision_of(reference) == expected_square_m
        # The reference names the SW corner, so the offset is bounded by the
        # diagonal of the square it names.
        back = grid_ref.to_latlon(reference)
        assert geodesy.distance_m(*EIFFEL, *back) <= expected_square_m * 1.5

    def test_dropping_digits_truncates_rather_than_rounds(self):
        """A coarser reference must name the square the point is in.

        Rounding would move a position near a square's north-east edge into the
        neighbouring square -- a kilometre away at 4 digits, and wrong.
        """
        full = grid_ref.from_latlon(*EIFFEL, 10)
        for digits in (2, 4, 6, 8):
            coarse = grid_ref.from_latlon(*EIFFEL, digits)
            half = digits // 2
            assert coarse == full[:5] + full[5 : 5 + half] + full[10 : 10 + half]

    @pytest.mark.parametrize(
        ("lat", "lon", "expected"),
        [
            (48.8584, 2.2945, "31UDQ4825211954"),
            (38.8895, -77.0353, "18SUJ2347806483"),
            (-33.8568, 151.2153, "56HLH3490052288"),
            (-22.9519, -43.2105, "23KPQ8347660687"),
            (64.1466, -21.9426, "27WVM5413813689"),
            (78.2232, 15.6267, "33XWG1427883355"),
            (89.9, 0.0, "ZAG0000088897"),
            (-89.9, 0.0, "BAN0000011102"),
            (0.0, 0.0, "31NAA6602100000"),
        ],
    )
    def test_golden_vectors(self, lat, lon, expected):
        assert grid_ref.from_latlon(lat, lon, 10) == expected

    def test_the_poles_are_covered_by_ups_not_left_undefined(self):
        """The polar zones use UPS, so there is no latitude where this fails."""
        for lat in (85.0, 90.0, -85.0, -90.0):
            assert grid_ref.from_latlon(lat, 0.0, 10)

    def test_the_published_usng_example_lands_on_the_washington_monument(self):
        """FGDC-STD-011-2001 uses 18S UJ 23480 06479 as its worked example."""
        lat, lon = grid_ref.to_latlon("18S UJ 23480 06479")
        assert geodesy.distance_m(lat, lon, 38.8895, -77.0353) < 60.0

    @pytest.mark.parametrize(
        "text",
        ["10S GJ 06832 44683", "10SGJ0683244683", "10s gj 06832 44683", "  10SGJ0683244683  "],
    )
    def test_spacing_and_case_are_irrelevant(self, text):
        assert grid_ref.normalize(text) == "10SGJ0683244683"
        assert grid_ref.is_grid_like(text)

    def test_odd_digit_count_is_rejected_with_a_usable_message(self):
        with pytest.raises(GeoInputError) as exc:
            grid_ref.precision_of("10SGJ068")
        message = str(exc.value)
        assert "even number of digits" in message
        assert "10S GJ 0683 4468" in message, "the message must show a valid example"

    def test_coordinates_are_not_mistaken_for_a_grid_reference(self):
        assert not grid_ref.is_grid_like("48.8584, 2.2945")
        assert not grid_ref.is_grid_like("hello world")

    def test_roundtrip_is_stable_across_the_globe(self):
        places = [
            (48.8584, 2.2945),
            (-33.8688, 151.2093),
            (40.7128, -74.0060),
            (35.6762, 139.6503),
            (-22.9068, -43.1729),
            (64.1466, -21.9426),
        ]
        for lat, lon in places:
            reference = grid_ref.from_latlon(lat, lon, 10)
            back = grid_ref.to_latlon(reference)
            assert geodesy.distance_m(lat, lon, *back) < 2.0

    @pytest.mark.parametrize(
        "reference",
        [
            "31UDQ4825211954",
            "18SUJ2347806483",
            "56HLH3490052288",
            "27WVM5413813689",
            "33XWG1427883355",
            "ZAG0000088897",
            "BAN0000011102",
        ],
    )
    def test_a_reference_survives_a_round_trip_unchanged(self, reference):
        """Convert to coordinates and straight back: the string must not drift.

        Regression test. The projection lands a micrometre *below* the square's
        own edge -- 48251.999999 rather than 48252 -- and truncating that names
        the square next door, so a reference handed back to the server came out
        one metre west and south of where it went in.
        """
        assert grid_ref.from_latlon(*grid_ref.to_latlon(reference), 10) == reference


class TestDmsFormatting:
    def test_hemisphere_letters(self):
        assert format_dms(48.8584, is_latitude=True).endswith("N")
        assert format_dms(-33.8688, is_latitude=True).endswith("S")
        assert format_dms(2.2945, is_latitude=False).endswith("E")
        assert format_dms(-74.0060, is_latitude=False).endswith("W")

    def test_roundtrip_through_the_parser(self):
        text = f"{format_dms(48.8584, is_latitude=True)} {format_dms(2.2945, is_latitude=False)}"
        parsed = parse_location(text)
        assert geodesy.distance_m(48.8584, 2.2945, *parsed.as_tuple()) < 3.0
