"""Polymorphic location parsing.

Every tool that takes a position takes a single ``location: str`` and routes it
through :func:`parse_location`. That is a deliberate design decision with two
motivations.

The practical one: a model should not have to know which of six mutually
exclusive parameter pairs a given tool wants. The obvious alternative -- an
``(grid_reference, latitude, longitude)`` triple on every tool -- spends three
parameters to express one concept and fails at runtime if you fill in the wrong
combination.

The second: parameter *ordering* is a stronger signal of intended audience than
any word in a docstring. Putting ``grid_reference`` first on every tool tells a
reader what kind of user was imagined. One polymorphic parameter that accepts
decimal degrees, DMS, USNG/MGRS, geohash, H3, Plus Codes and place names treats
them all as equal citizens, with decimal lat/lon documented as canonical.

Supported forms::

    "48.8584, 2.2945"          decimal degrees (canonical)
    "48.8584 2.2945"           whitespace-separated
    "48°51'30\\"N 2°17'40\\"E"   degrees/minutes/seconds
    "48 51.5 N, 2 17.6 E"      degrees/decimal-minutes
    "31UDQ4825211954"          USNG / MGRS grid reference
    "u09tunquc"                geohash
    "geohash:u09tunquc"        geohash, marked so it is never taken for a place name
    "891fb46741bffff"          H3 cell index
    "8FW4V75V+9R6"             Plus Code (Open Location Code)
    "Eiffel Tower, Paris"      place name -> needs geocoding by the caller

A word such as "Bergen", "Denver" or "Sydney" is spelt entirely from the
geohash alphabet, and decoding it lands in Alaska, the Atlantic or Turkey. So a
bare token is read as a geohash straight away only when it is written the way
geohashes are: lowercase, with a digit among the letters. Any other token that
could be one is a place name that is *also* a valid geohash
(``Location.ambiguous_geohash``); the caller geocodes it first and falls back
to the geohash only if no place has that name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import h3

from ..errors import GeoInputError
from . import grid_ref, grids

__all__ = [
    "GEOHASH_PREFIX",
    "Location",
    "LocationKind",
    "as_geohash",
    "format_dms",
    "looks_like_place_name",
    "parse_location",
]

LocationKind = Literal["latlon", "dms", "grid", "geohash", "h3", "pluscode", "place_name"]


@dataclass(frozen=True)
class Location:
    """A parsed location.

    ``latitude``/``longitude`` are ``None`` only when ``kind == "place_name"``,
    in which case the caller must geocode ``raw`` first.
    """

    raw: str
    kind: LocationKind
    latitude: float | None = None
    longitude: float | None = None
    #: Side of the named cell in metres, where the notation implies an area
    #: (grid square, geohash cell, H3 hexagon). ``None`` for point notations.
    precision_m: float | None = None
    #: For a geocoded place name: the full name of the place it resolved to,
    #: and of any far-away places of similar prominence it could have meant.
    display_name: str | None = None
    alternatives: tuple[str, ...] = ()
    #: True when ``raw`` is a bare word that is also a valid geohash, such as
    #: "Bergen" or "gcpvj". Such a word is geocoded first; ``kind`` says which
    #: reading was used: ``place_name`` if a place matched, ``geohash`` if not.
    ambiguous_geohash: bool = False

    @property
    def geocoded(self) -> bool:
        """True when coordinates came from geocoding a place name."""
        return self.kind == "place_name" and self.resolved

    @property
    def resolved(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def as_tuple(self) -> tuple[float, float]:
        if not self.resolved:
            raise GeoInputError(
                f"{self.raw!r} is a place name and must be geocoded before use. "
                "Call place_geocode first, or pass coordinates directly."
            )
        assert self.latitude is not None and self.longitude is not None
        return self.latitude, self.longitude


# --- detection patterns ----------------------------------------------------

_DECIMAL_PAIR = re.compile(r"^\s*([+-]?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*([+-]?\d{1,3}(?:\.\d+)?)\s*$")
# Any of: degree sign, prime, double-prime, or a trailing hemisphere letter.
_DMS_HINT = re.compile(r"[°'\"′″]|(?<![A-Za-z])[NSEW](?![A-Za-z])", re.IGNORECASE)
_DMS_COMPONENT = re.compile(
    r"""(?P<deg>[+-]?\d+(?:\.\d+)?)\s*[°°d]?\s*
        (?:(?P<min>\d+(?:\.\d+)?)\s*['′m]?\s*)?
        (?:(?P<sec>\d+(?:\.\d+)?)\s*["″s]?\s*)?
        (?P<hem>[NSEW])?""",
    re.IGNORECASE | re.VERBOSE,
)
_GEOHASH_RE = re.compile(r"^[0123456789bcdefghjkmnpqrstuvwxyz]{1,12}$")
_H3_RE = re.compile(r"^[0-9a-f]{15,16}$", re.IGNORECASE)

#: Marks the rest of a location as a geohash, whatever else it could be read as.
GEOHASH_PREFIX = "geohash:"
#: The shortest unmarked token considered as a geohash. Shorter ones ("NYC",
#: "UK") are names, and a 3-character geohash is a cell 156 km across.
_GEOHASH_MIN_UNMARKED = 4


def _unmarked_geohash(text: str) -> Literal["geohash", "ambiguous"] | None:
    """How a token without the ``geohash:`` prefix reads as a geohash, if at all.

    Geohashes are written in lowercase, and most carry a digit: 85% of
    5-character ones and 97% of 9-character ones. A token that is lowercase and
    mixes digits with letters ("u09tunquc") is a geohash. One without a digit
    ("Bergen", "Sydney", "gcpvj"), with capitals ("SW19") or with only digits
    (the postcode "90210") is also valid geohash text, but far more often a
    place, so it is only ``ambiguous``.
    """
    low = text.lower()
    if len(low) < _GEOHASH_MIN_UNMARKED or not _GEOHASH_RE.match(low):
        return None
    has_digit = any(c.isdigit() for c in low)
    has_letter = any(c.isalpha() for c in low)
    if text == low and has_digit and has_letter:
        return "geohash"
    return "ambiguous"


def _marked_geohash(text: str) -> str | None:
    """The code after a ``geohash:`` prefix (any case), or None if unmarked."""
    if text[: len(GEOHASH_PREFIX)].lower() != GEOHASH_PREFIX:
        return None
    return text[len(GEOHASH_PREFIX) :].strip()


def as_geohash(code: str, *, raw: str | None = None) -> Location:
    """Read ``code`` as a geohash, whatever else it could be read as."""
    low = code.strip().lower()
    if not _GEOHASH_RE.match(low):
        raise GeoInputError.with_example(
            got=raw if raw is not None else code,
            problem=(
                "A geohash is 1 to 12 characters from its own alphabet: the digits "
                "and the letters other than a, i, l and o."
            ),
            example=f"{GEOHASH_PREFIX}u09tunquc",
        )
    lat, lon = grids.geohash_decode(low)
    return Location(
        raw=raw if raw is not None else code.strip(),
        kind="geohash",
        latitude=lat,
        longitude=lon,
        precision_m=float(grids.GEOHASH_PRECISION_M.get(len(low), 1)),
    )


def looks_like_place_name(value: str) -> bool:
    """True if the string is best treated as free text for a geocoder."""
    text = value.strip()
    return not any(
        (
            _marked_geohash(text) is not None,
            _DECIMAL_PAIR.match(value),
            grid_ref.is_grid_like(value),
            "+" in value,
            _H3_RE.match(text) and _is_valid_h3(text),
            _unmarked_geohash(text) == "geohash",
            _DMS_HINT.search(value),
        )
    )


def _is_valid_h3(value: str) -> bool:
    try:
        return bool(h3.is_valid_cell(value.lower()))
    except Exception:
        return False


def _parse_dms(text: str) -> tuple[float, float]:
    """Parse a degrees/minutes/seconds or degrees/decimal-minutes pair."""
    matches = [m for m in _DMS_COMPONENT.finditer(text) if m.group("deg")]
    if len(matches) < 2:
        raise GeoInputError.with_example(
            got=text,
            problem="Could not read two DMS components (latitude and longitude).",
            example="48°51'30\"N 2°17'40\"E",
        )
    values: list[tuple[float, str | None]] = []
    for m in matches[:2]:
        deg = float(m.group("deg"))
        minutes = float(m.group("min") or 0.0)
        seconds = float(m.group("sec") or 0.0)
        sign = -1.0 if deg < 0 else 1.0
        magnitude = abs(deg) + minutes / 60.0 + seconds / 3600.0
        values.append((sign * magnitude, (m.group("hem") or "").upper() or None))

    def apply(value: float, hem: str | None) -> float:
        if hem in {"S", "W"}:
            return -abs(value)
        if hem in {"N", "E"}:
            return abs(value)
        return value

    first, second = values
    # If hemispheres are given, trust them over ordering: "2E 48N" is valid.
    if first[1] in {"E", "W"} and second[1] in {"N", "S"}:
        lon, lat = apply(*first), apply(*second)
    else:
        lat, lon = apply(*first), apply(*second)
    return lat, lon


def _validate_range(lat: float, lon: float, raw: str) -> tuple[float, float]:
    if not -90.0 <= lat <= 90.0:
        raise GeoInputError.with_example(
            got=raw,
            problem=f"Latitude must be between -90 and 90; got {lat}. "
            "If you meant longitude first, swap the order -- this parser expects "
            "latitude first, as in GeoJSON's reverse.",
            example="48.8584, 2.2945",
        )
    if not -180.0 <= lon <= 180.0:
        raise GeoInputError.with_example(
            got=raw,
            problem=f"Longitude must be between -180 and 180; got {lon}.",
            example="48.8584, 2.2945",
        )
    return lat, lon


def parse_location(value: str) -> Location:
    """Parse any supported location notation into a :class:`Location`.

    Detection is ordered from least to most ambiguous. Place names are the
    fallback, never a guess: anything that structurally matches a coordinate
    notation is parsed as one. The exception is a word that is only *also* a
    geohash, such as "Bergen": it comes back as a place name with
    ``ambiguous_geohash`` set, for the caller to geocode first.
    """
    if value is None or not str(value).strip():
        raise GeoInputError.with_example(
            got=value,
            problem="Location is empty.",
            example="48.8584, 2.2945",
        )
    raw = str(value).strip()

    # 0. An explicit "geohash:" prefix overrides every other reading, and is
    #    checked before DMS because "geohash:s000" holds a bare hemisphere letter.
    marked = _marked_geohash(raw)
    if marked is not None:
        return as_geohash(marked, raw=raw)

    # 1. Decimal degrees -- the canonical form, checked first and cheapest.
    m = _DECIMAL_PAIR.match(raw)
    if m:
        lat, lon = _validate_range(float(m.group(1)), float(m.group(2)), raw)
        return Location(raw=raw, kind="latlon", latitude=lat, longitude=lon)

    # 2. USNG / MGRS. Checked before geohash: "10SGJ" is structurally both,
    #    and the grid reference is the more specific pattern.
    if grid_ref.is_grid_like(raw):
        lat, lon = grid_ref.to_latlon(raw)
        return Location(
            raw=raw,
            kind="grid",
            latitude=lat,
            longitude=lon,
            precision_m=float(grid_ref.precision_of(raw)),
        )

    # 3. Plus Code -- the '+' is unambiguous.
    if "+" in raw:
        lat, lon = grids.pluscode_decode(raw)
        return Location(raw=raw, kind="pluscode", latitude=lat, longitude=lon)

    # 4. H3 -- validated against the library, not just the character class.
    if _H3_RE.match(raw) and _is_valid_h3(raw):
        lat, lon = grids.h3_decode(raw.lower())
        return Location(
            raw=raw,
            kind="h3",
            latitude=lat,
            longitude=lon,
            precision_m=(grids.h3_cell_area_km2(raw.lower()) ** 0.5) * 1000.0,
        )

    # 5. DMS / DDM -- requires a degree sign, prime or hemisphere letter.
    if _DMS_HINT.search(raw):
        lat, lon = _validate_range(*_parse_dms(raw), raw)
        return Location(raw=raw, kind="dms", latitude=lat, longitude=lon)

    # 6. Geohash. Last of the coordinate forms because base-32 strings collide
    #    with real place names: "Bergen", "Denver" and "Sydney" are all valid
    #    geohashes. Only the unmistakable form is decoded here; see
    #    _unmarked_geohash for the rule.
    reading = _unmarked_geohash(raw)
    if reading == "geohash":
        return as_geohash(raw)

    # 7. Free text for a geocoder, flagged when it could also be a geohash.
    return Location(raw=raw, kind="place_name", ambiguous_geohash=reading == "ambiguous")


def format_dms(decimal_degrees: float, *, is_latitude: bool) -> str:
    """Format decimal degrees as ``48°51'30.2"N``."""
    hemisphere = (
        ("N" if decimal_degrees >= 0 else "S")
        if is_latitude
        else ("E" if decimal_degrees >= 0 else "W")
    )
    magnitude = abs(decimal_degrees)
    degrees = int(magnitude)
    minutes_decimal = (magnitude - degrees) * 60.0
    minutes = int(minutes_decimal)
    seconds = (minutes_decimal - minutes) * 60.0
    return f"{degrees}°{minutes:02d}'{seconds:04.1f}\"{hemisphere}"
