"""Place names: which place a tool used, and when it should refuse to guess.

Regression tests. `resolve()` -- the geocoding path under every tool that takes
a location -- used to ask Nominatim for one result and take it. "Springfield"
silently became Springfield, Illinois; nothing in the response said so; and
three tools did not credit Nominatim at all despite its ODbL terms.

The candidate rankings below are real: Nominatim's own importance scores and
positions for these queries, as returned on 2026-09-24 (Springfield, Paris,
Portland) and 2026-10-11 (Denver, Bergen, Berne, Málaga). The thresholds in
`tools/_shared.py` were calibrated against them. Nominatim found nothing for
"gcpvj" on 2026-10-11, and the fake below returns nothing for any query it
has no ranking for.
"""

from __future__ import annotations

from typing import Any

import pytest

from geospatial_mcp.errors import GeoInputError, GeoNoResults, GeoUpstreamError
from geospatial_mcp.net.providers import nominatim
from geospatial_mcp.tools._shared import (
    MENTION_PROMINENCE,
    far_alternatives,
    meta,
    resolve,
)
from tests.conftest import mcp_client


def _match(
    name: str,
    lat: float,
    lon: float,
    importance: float,
    state: str | None,
    **address: str,
) -> dict[str, Any]:
    return {
        "lat": str(lat),
        "lon": str(lon),
        "display_name": name,
        "importance": importance,
        "osm_type": "relation",
        "osm_id": abs(hash(name)) % 10_000_000,
        "category": "boundary",
        "type": "administrative",
        "address": {"state": state, **address} if state else address,
    }


RANKINGS: dict[str, list[dict[str, Any]]] = {
    "Springfield": [
        _match(
            "Springfield, Sangamon County, Illinois, United States",
            39.79902,
            -89.64396,
            0.613,
            "Illinois",
        ),
        _match(
            "Springfield, Hampden County, Massachusetts, United States",
            42.10148,
            -72.58981,
            0.611,
            "Massachusetts",
        ),
        _match(
            "Springfield, Greene County, Missouri, United States",
            37.20898,
            -93.29230,
            0.596,
            "Missouri",
        ),
    ],
    "Paris": [
        _match(
            "Paris, Île-de-France, France métropolitaine, France",
            48.85889,
            2.32004,
            0.897,
            "Île-de-France",
        ),
        _match(
            "Paris, Île-de-France, France métropolitaine, France",
            48.85349,
            2.34839,
            0.897,
            "Île-de-France",
        ),
        _match("Paris, Lamar County, Texas, United States", 33.66180, -95.55551, 0.530, "Texas"),
    ],
    "Portland": [
        _match(
            "Portland, Multnomah County, Oregon, United States",
            45.52023,
            -122.67419,
            0.710,
            "Oregon",
        ),
        _match(
            "Portland, Cumberland County, Maine, United States", 43.65910, -70.25682, 0.604, "Maine"
        ),
    ],
    # Spelt entirely from the geohash alphabet, and decoded as one until October 2026.
    "Denver": [
        _match("Denver, Colorado, United States", 39.73924, -104.98486, 0.701, "Colorado"),
        _match(
            "Denver, Bremer County, Iowa, 50622, United States", 42.67138, -92.33722, 0.459, "Iowa"
        ),
    ],
    "Bergen": [
        _match("Bergen, Vestland, Norge", 60.39431, 5.32592, 0.699, None, county="Vestland"),
        _match(
            "Mons, Hainaut, Wallonie, België / Belgique / Belgien",
            50.45496,
            3.95196,
            0.623,
            "Hainaut",
        ),
        _match(
            "Bergen County, New Jersey, United States", 40.96783, -74.05633, 0.561, "New Jersey"
        ),
    ],
    "Berne": [
        _match(
            "Bern, Verwaltungskreis Bern-Mittelland, Verwaltungsregion Bern-Mittelland, "
            "Bern/Berne, Schweiz/Suisse/Svizzera/Svizra",
            46.94847,
            7.45217,
            0.720,
            "Bern/Berne",
        ),
        _match(
            "Berné, Pontivy, Morbihan, Bretagne, France métropolitaine, 56240, France",
            47.99336,
            -3.39142,
            0.525,
            "Bretagne",
        ),
    ],
    "Málaga": [
        _match(
            "Málaga, Málaga-Costa del Sol, Málaga, Andalucía, España",
            36.76479,
            -4.44220,
            0.678,
            "Andalucía",
        ),
        _match(
            "Málaga, García Rovira, Santander, RAP Gran Santander, Colombia",
            6.69966,
            -72.73246,
            0.427,
            "Santander",
        ),
    ],
}


class _FakeClient:
    """Stands in for the shared HTTP client, recording what was asked for."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def get_json(self, url: str, *, params: dict[str, Any], provider: str, **_: Any):
        self.requests.append(dict(params))
        ranked = RANKINGS.get(params["q"], [])
        return ranked[: params["limit"]], False


@pytest.fixture
def fake_nominatim(monkeypatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(nominatim, "get_client", lambda: client)
    return client


def _ranked(query: str) -> list[dict[str, Any]]:
    return [
        {**r, "latitude": float(r["lat"]), "longitude": float(r["lon"])} for r in RANKINGS[query]
    ]


class TestTheAmbiguityRule:
    def test_near_tied_and_far_apart_is_ambiguous(self):
        rivals = far_alternatives(_ranked("Springfield"), within=0.05)
        assert [r["address"]["state"] for r in rivals] == ["Massachusetts", "Missouri"]

    def test_near_tied_but_the_same_place_is_not(self):
        """Paris returns the city and its boundary 2 km apart, at equal importance."""
        assert far_alternatives(_ranked("Paris"), within=0.05) == []

    def test_far_apart_but_much_less_prominent_is_not(self):
        """Paris, Texas is real, and not what anyone asking for "Paris" means."""
        assert far_alternatives(_ranked("Paris"), within=MENTION_PROMINENCE) == []

    def test_a_clear_favourite_still_mentions_a_close_second(self):
        rivals = far_alternatives(_ranked("Portland"), within=MENTION_PROMINENCE)
        assert [r["address"]["state"] for r in rivals] == ["Maine"]
        assert far_alternatives(_ranked("Portland"), within=0.05) == []

    def test_one_place_returned_as_two_objects_is_listed_once(self):
        """Nominatim returns Newcastle, NSW as a relation and its centre node."""
        ranked = [
            {"latitude": 54.97385, "longitude": -1.61316, "importance": 0.675},
            {"latitude": -32.92953, "longitude": 151.78010, "importance": 0.602},
            {"latitude": -32.92700, "longitude": 151.77650, "importance": 0.602},
        ]
        assert len(far_alternatives(ranked, within=MENTION_PROMINENCE)) == 1

    def test_a_single_candidate_is_never_ambiguous(self):
        assert far_alternatives(_ranked("Portland")[:1], within=1.0) == []


class TestResolve:
    def test_an_ambiguous_name_is_refused_with_the_candidates(self, fake_nominatim):
        with pytest.raises(GeoInputError) as exc:
            resolve("Springfield")
        message = str(exc.value)
        assert "Illinois" in message and "Massachusetts" in message
        assert "39.79902,-89.64396" in message, "candidates must be usable as input"
        assert "'Springfield, Illinois'" in message, "must suggest a qualified name"

    def test_a_clear_name_resolves_and_says_to_what(self, fake_nominatim):
        loc = resolve("Portland")
        assert loc.geocoded
        assert "Oregon" in (loc.display_name or "")
        assert any("Maine" in a for a in loc.alternatives)

    def test_coordinates_never_touch_the_geocoder(self, fake_nominatim):
        loc = resolve("45.52,-122.67")
        assert not loc.geocoded
        assert fake_nominatim.requests == []


class TestCacheSharing:
    def test_resolve_and_place_geocode_send_the_same_request(self, fake_nominatim):
        """The cache keys on params; different limits would split it in two."""
        resolve("Portland")

        async def geocode_once():
            async with mcp_client() as client:
                await client.call_tool("place_geocode", {"query": "Portland"})

        import anyio

        anyio.run(geocode_once)
        assert len(fake_nominatim.requests) == 2
        assert fake_nominatim.requests[0] == fake_nominatim.requests[1]


class TestDisclosureAndAttribution:
    def test_meta_credits_nominatim_and_names_the_place(self, fake_nominatim):
        m = meta("open-meteo", resolved=[resolve("Portland")])
        assert m.sources == ["open-meteo", "nominatim"]
        assert any("OpenStreetMap" in line for line in m.attribution)
        assert m.notes[0].startswith("Geocoded 'Portland' as Portland, Multnomah County, Oregon")
        assert "Maine" in m.notes[0]

    def test_coordinates_add_no_source_and_no_note(self):
        m = meta(offline=True, resolved=[resolve("45.52,-122.67")])
        assert m.sources == []
        assert m.offline
        assert m.notes == []

    async def test_a_tool_reports_what_it_geocoded(self, fake_nominatim):
        async with mcp_client() as client:
            result = await client.call_tool("time_at_location", {"location": "Portland"})
        body = result.structured_content
        assert "nominatim" in body["meta"]["sources"]
        assert not body["meta"]["offline"]
        assert "Oregon" in body["meta"]["notes"][0]

    async def test_a_tool_refuses_an_ambiguous_name(self, fake_nominatim):
        async with mcp_client() as client:
            result = await client.call_tool("time_at_location", {"location": "Springfield"})
        assert result.is_error
        text = "".join(getattr(b, "text", "") for b in result.content or [])
        assert "Illinois" in text and "Massachusetts" in text

    async def test_place_geocode_mentions_rivals_it_did_not_return(self, fake_nominatim):
        async with mcp_client() as client:
            result = await client.call_tool("place_geocode", {"query": "Springfield"})
        body = result.structured_content
        assert len(body["matches"]) == 1
        assert any("Massachusetts" in n and "limit=5" in n for n in body["meta"]["notes"])

    async def test_place_geocode_is_not_refused(self, fake_nominatim):
        """place_geocode is how a model disambiguates; it must never refuse."""
        async with mcp_client() as client:
            result = await client.call_tool("place_geocode", {"query": "Springfield", "limit": 5})
        assert not result.is_error
        assert len(result.structured_content["matches"]) == 3


class _UnreachableClient(_FakeClient):
    def get_json(self, url: str, *, params: dict[str, Any], provider: str, **_: Any):
        self.requests.append(dict(params))
        raise GeoUpstreamError(f"Could not reach {provider}: [Errno 8] nodename nor servname")


class TestGeohashOrPlaceName:
    """A word that is also a valid geohash is looked up as a place first.

    Regression tests. "Denver" used to be decoded as a geohash, with no note,
    to 17.88, -57.86 in the Atlantic; "Bergen" and "Berne" landed in Alaska.
    """

    @pytest.mark.parametrize(
        ("name", "lat", "lon", "fragment"),
        [
            ("Denver", 39.73924, -104.98486, "Colorado"),
            ("Bergen", 60.39431, 5.32592, "Vestland"),
            ("Berne", 46.94847, 7.45217, "Bern"),
        ],
    )
    def test_a_city_spelt_in_the_geohash_alphabet_is_geocoded(
        self, fake_nominatim, name, lat, lon, fragment
    ):
        loc = resolve(name)
        assert loc.geocoded
        assert loc.as_tuple() == pytest.approx((lat, lon))
        assert fragment in (loc.display_name or "")
        assert loc.ambiguous_geohash
        assert [r["q"] for r in fake_nominatim.requests] == [name]

    def test_the_note_names_the_place_and_the_other_reading(self, fake_nominatim):
        note = meta(resolved=[resolve("Denver")]).notes[0]
        assert note.startswith("Geocoded 'Denver' as Denver, Colorado, United States")
        assert "also a valid geohash" in note
        assert "17.88300, -57.85950" in note, "the note must say where the other reading is"
        assert "'geohash:denver'" in note, "the note must say how to ask for it"

    def test_a_name_outside_the_alphabet_gets_no_geohash_note(self, fake_nominatim):
        loc = resolve("Málaga")
        assert loc.geocoded and not loc.ambiguous_geohash
        assert loc.as_tuple() == pytest.approx((36.76479, -4.44220))
        assert "geohash" not in meta(resolved=[loc]).notes[0]

    def test_a_geohash_with_no_digit_is_used_when_no_place_matches(self, fake_nominatim):
        loc = resolve("gcpvj")
        assert loc.kind == "geohash"
        assert loc.ambiguous_geohash
        assert loc.as_tuple() == pytest.approx((51.52588, -0.10986), abs=1e-4)
        assert [r["q"] for r in fake_nominatim.requests] == ["gcpvj"]
        m = meta(offline=True, resolved=[loc])
        assert m.notes[0].startswith("No place called 'gcpvj' was found")
        assert "'geohash:gcpvj'" in m.notes[0]
        assert m.sources == [], "nothing from Nominatim was used, so nothing is owed"
        assert not m.offline, "a lookup was made"

    @pytest.mark.parametrize("text", ["u09tunquc", "eysb3kxmm", "geohash:gcpvj"])
    def test_an_unmistakable_geohash_never_touches_the_geocoder(self, fake_nominatim, text):
        loc = resolve(text)
        assert loc.kind == "geohash"
        assert fake_nominatim.requests == []
        m = meta(offline=True, resolved=[loc])
        assert m.offline
        assert m.notes == []

    def test_an_unknown_name_is_still_an_error(self, fake_nominatim):
        """Only a word that is also a geohash has another reading to fall back on."""
        with pytest.raises(GeoNoResults):
            resolve("Atlantis")

    def test_a_coordinates_only_tool_says_how_to_mark_a_geohash(self, fake_nominatim):
        with pytest.raises(GeoInputError) as exc:
            resolve("gcpvj", what="point", allow_geocode=False)
        assert "'geohash:gcpvj'" in str(exc.value)
        assert fake_nominatim.requests == []

    def test_an_unreachable_geocoder_says_how_to_skip_it(self, monkeypatch):
        client = _UnreachableClient()
        monkeypatch.setattr(nominatim, "get_client", lambda: client)
        with pytest.raises(GeoUpstreamError) as exc:
            resolve("gcpvj")
        assert "Could not reach Nominatim" in str(exc.value)
        assert "'geohash:gcpvj'" in str(exc.value)
        with pytest.raises(GeoUpstreamError) as plain:
            resolve("Berlin")
        assert "geohash" not in str(plain.value)

    async def test_coord_convert_geocodes_denver(self, fake_nominatim):
        async with mcp_client() as client:
            result = await client.call_tool("coord_convert", {"location": "Denver"})
        body = result.structured_content
        assert body["detected_format"] == "place name (geocoded)"
        assert body["formats"]["latitude"] == pytest.approx(39.73924)
        assert body["formats"]["longitude"] == pytest.approx(-104.98486)
        assert "'geohash:denver'" in body["meta"]["notes"][0]

    async def test_coord_convert_reports_the_geohash_it_fell_back_to(self, fake_nominatim):
        async with mcp_client() as client:
            result = await client.call_tool("coord_convert", {"location": "gcpvj"})
        body = result.structured_content
        assert body["detected_format"] == "geohash"
        assert body["input_precision_m"] == 4890.0
        assert body["formats"]["latitude"] == pytest.approx(51.52588, abs=1e-4)
        assert body["meta"]["notes"][0].startswith("No place called 'gcpvj'")

    async def test_coord_describe_names_both_readings_offline(self, fake_nominatim):
        async with mcp_client() as client:
            word = (
                await client.call_tool("coord_describe", {"value": "Bergen"})
            ).structured_content
            marked = (
                await client.call_tool("coord_describe", {"value": "geohash:gcpvj"})
            ).structured_content
        assert fake_nominatim.requests == []
        assert not word["is_valid"]
        assert word["detected_format"] == "place_name"
        assert "63.93219, -146.46423" in word["problem"]
        assert "'geohash:bergen'" in word["problem"]
        assert marked["is_valid"]
        assert marked["detected_format"] == "geohash"
        assert marked["implied_precision_m"] == 4890.0
