"""Helpers shared across tool modules."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ..attribution import attribution_for
from ..errors import GeoInputError
from ..geo import geodesy
from ..geo.coords import Location, parse_location
from ..models import ResponseMeta

__all__ = [
    "MENTION_PROMINENCE",
    "GeometryInput",
    "as_geometry_text",
    "clamp",
    "far_alternatives",
    "meta",
    "require_metres",
    "resolve",
    "resolve_pair",
]

#: When is a place name ambiguous? Calibrated against live Nominatim rankings.
#:
#: Nominatim's ``importance`` (0-1) is its estimate of how prominent a place is.
#: Two candidates are rival readings of one name when their importance is close
#: *and* they are far apart -- "Springfield" returns Illinois at 0.613 and
#: Massachusetts at 0.611, 1456 km away. Closeness alone is not enough: "Paris"
#: returns two objects at 0.897, the city relation and its administrative
#: boundary, 2 km apart, which are the same place. Distance alone is not enough
#: either: "Paris" also returns Paris, Texas, but at 0.530, and nobody asking for
#: "Paris" is owed a question about Texas.
#:
#: Within ``_TIE_PROMINENCE`` the tool refuses to guess ("Springfield",
#: "Cambridge"). Within ``MENTION_PROMINENCE`` it answers, but names the others
#: ("Portland" -> Oregon, also Maine; "Newcastle" -> upon Tyne, also NSW).
_TIE_PROMINENCE = 0.05
MENTION_PROMINENCE = 0.15
_FAR_APART_M = 50_000.0
_NAME_CHARS = 110

#: A geometry argument as it can actually arrive over the wire.
#:
#: The MCP SDK parses any JSON-looking string argument into a Python object
#: before validating it against the tool signature. A parameter declared ``str``
#: that receives ``'{"type":"Point",...}'`` is therefore handed a ``dict`` and
#: fails with "Input should be a valid string" -- even though the client sent
#: exactly what the description asked for. Models also legitimately send GeoJSON
#: as a nested object rather than a string. Accepting both is the only
#: interoperable choice, and ``as_geometry_text`` normalises it.
GeometryInput = str | dict[str, Any] | list[Any]


def meta(
    *sources: str,
    offline: bool = False,
    cached: bool = False,
    truncated: bool = False,
    notes: list[str] | None = None,
    resolved: Iterable[Location] = (),
) -> ResponseMeta:
    """Build a :class:`ResponseMeta` with attribution filled in from sources.

    Pass every location the tool resolved as ``resolved``. Any that were place
    names add Nominatim to the sources -- its data is ODbL and the attribution is
    owed -- and a note saying exactly which place the name was taken to mean.
    Without that note a response to "weather in Springfield" is a correct answer
    about a place the user may not have meant, with nothing to show which.
    """
    geocoded = [loc for loc in resolved if loc.geocoded]
    all_sources = list(dict.fromkeys([*sources, *(["nominatim"] if geocoded else [])]))
    return ResponseMeta(
        sources=all_sources,
        attribution=attribution_for(*all_sources),
        offline=offline if not all_sources else False,
        cached=cached,
        truncated=truncated,
        notes=[*(_geocoding_note(loc) for loc in geocoded), *(notes or [])],
    )


def _short(name: str) -> str:
    return name if len(name) <= _NAME_CHARS else name[: _NAME_CHARS - 1].rstrip(", ") + "…"


def _geocoding_note(loc: Location) -> str:
    lat, lon = loc.as_tuple()
    note = f"Geocoded {loc.raw!r} as {_short(loc.display_name or loc.raw)} ({lat:.5f}, {lon:.5f})."
    if loc.alternatives:
        others = "; ".join(_short(a) for a in loc.alternatives)
        note += f" Other places share the name: {others}. Pass coordinates to use one of those."
    return note


def far_alternatives(results: Sequence[dict[str, Any]], *, within: float) -> list[dict[str, Any]]:
    """Candidates nearly as prominent as the top one, but somewhere else entirely.

    ``results`` are Nominatim matches in its own rank order. See the constants
    above for how the thresholds were chosen.
    """
    if len(results) < 2:
        return []
    top = results[0]
    top_importance = top.get("importance")
    if top_importance is None:
        return []
    rivals: list[dict[str, Any]] = []
    for candidate in results[1:]:
        importance = candidate.get("importance")
        if importance is None or top_importance - importance > within:
            continue
        # Far from the top match, and from every rival already kept: Nominatim
        # often returns one town as two OSM objects (the relation and its centre
        # node), and listing Newcastle, New South Wales twice helps nobody.
        if all(_apart(candidate, kept) for kept in [top, *rivals]):
            rivals.append(candidate)
    return rivals


def _apart(a: dict[str, Any], b: dict[str, Any]) -> bool:
    separation = geodesy.distance_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    return separation > _FAR_APART_M


def _qualifier(match: dict[str, Any]) -> str | None:
    address = match.get("address") or {}
    return address.get("state") or address.get("county") or address.get("country")


def resolve(location: str, *, what: str = "location", allow_geocode: bool = True) -> Location:
    """Parse a location, geocoding a place name if one was given.

    Geocoding here rather than in each tool is what lets every tool accept
    ``"Eiffel Tower"`` as readily as ``"48.8584,2.2945"``.
    """
    parsed = parse_location(location)
    if parsed.resolved:
        return parsed
    if not allow_geocode:
        raise GeoInputError.with_example(
            got=location,
            problem=f"The {what} must be coordinates, not a place name, for this tool.",
            example="48.8584,2.2945",
        )
    from ..net.providers import nominatim

    results, _ = nominatim.geocode(parsed.raw, limit=nominatim.CANDIDATES)
    best = results[0]
    rivals = far_alternatives(results, within=_TIE_PROMINENCE)
    if rivals:
        # Picking one would be a guess, and a confident wrong place is worse than
        # a question: every number downstream would be right for somewhere else.
        listed = "; ".join(
            f"{_short(r['display_name'])} ({r['latitude']:.5f},{r['longitude']:.5f})"
            for r in [best, *rivals][:4]
        )
        qualifier = _qualifier(best)
        qualified = f"'{parsed.raw}, {qualifier}', or " if qualifier else ""
        raise GeoInputError.with_example(
            got=location,
            problem=(
                f"The {what} {parsed.raw!r} matches several places of similar prominence, "
                f"far apart: {listed}. Add a region or country, or pass coordinates."
            ),
            example=f"{qualified}coordinates such as '{best['latitude']:.5f},{best['longitude']:.5f}'",
        )
    return Location(
        raw=parsed.raw,
        kind="place_name",
        latitude=best["latitude"],
        longitude=best["longitude"],
        display_name=best.get("display_name") or None,
        alternatives=tuple(
            r["display_name"]
            for r in far_alternatives(results, within=MENTION_PROMINENCE)[:2]
            if r.get("display_name")
        ),
    )


def resolve_pair(
    a: str, b: str, *, names: tuple[str, str] = ("start", "end")
) -> tuple[Location, Location]:
    """Resolve two locations, labelling errors with which one failed."""
    try:
        first = resolve(a, what=names[0])
    except GeoInputError as exc:
        raise GeoInputError(f"{names[0]}: {exc}") from exc
    try:
        second = resolve(b, what=names[1])
    except GeoInputError as exc:
        raise GeoInputError(f"{names[1]}: {exc}") from exc
    return first, second


def as_geometry_text(value: GeometryInput | None) -> str | None:
    """Normalise a geometry argument to text, whether it arrived as JSON or a string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    import json

    return json.dumps(value)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


#: The smallest distance a metres parameter accepts. Below it, the value is
#: almost always a distance in degrees: a model that converts 7.5 km to 0.0674
#: and passes it as metres gets a 7 cm buffer, or a search radius that finds
#: nothing and is then reported as an absence of data. A genuine sub-metre
#: buffer is possible with survey-grade input, but rare in this server's work,
#: and an error the model can correct beats a silently wrong answer.
MIN_METRES = 1.0


def require_metres(value: float, *, name: str, example: str) -> float:
    """Reject a non-zero distance too small to be metres; it is probably degrees."""
    if 0 < abs(value) < MIN_METRES:
        raise GeoInputError.with_example(
            got=value,
            problem=(
                f"{name} is in metres, and {value:g} m is under {MIN_METRES:g} m -- "
                "that looks like a distance in degrees. Convert it to metres."
            ),
            example=example,
        )
    return value
