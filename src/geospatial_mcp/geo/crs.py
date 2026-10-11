"""Coordinate reference system transforms, with honest accuracy reporting.

The reason this module exists rather than a one-line ``pyproj.transform`` call:
PROJ ships without the high-accuracy datum-shift grids (NTv2, NADCON, geoid
models). When a transform needs one and it is absent, PROJ does not fail -- it
silently falls back to a three-parameter Helmert shift that can be several
metres out. Most tooling hides this. A surveyor or an insurance underwriter
placing a boundary needs to know.

So every transform reports the operation PROJ actually used for *this* point
and the accuracy PROJ publishes for it, flags ``ballpark=True`` when PROJ found
no published transformation at all and fell back to ignoring the datum shift,
and names the grid file a more accurate operation would need.

The operation is read after the transform, per point, rather than from the
transformer. A transformer built with ``from_crs`` picks an operation at
transform time by area of use -- NAD27 alone has dozens, one per region -- so
"the transformer's accuracy" is not a meaningful number until a point has gone
through it, and PROJ reports -1 (unknown) before then.

Network grids stay opt-in. ``PROJ_NETWORK=ON`` lets PROJ fetch grids from
cdn.proj.org on demand, which turns a local computation into a network call;
where that fetch fails -- a firewall, a proxy -- PROJ returns infinities rather
than an error, and :func:`transform_point` falls back to the best operation
that does not need the missing grid, saying so.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import pyproj.network
from pyproj import CRS, Transformer
from pyproj.transformer import AreaOfInterest, TransformerGroup

from ..errors import GeoInputError

__all__ = ["TransformResult", "describe_crs", "transform_point", "utm_crs_for", "utm_zone_of"]


@dataclass(frozen=True)
class TransformResult:
    x: float
    y: float
    source_epsg: str
    target_epsg: str
    accuracy_m: float | None
    ballpark: bool
    pipeline: str
    #: A more accurate operation exists for this point but needs grid files
    #: that are not installed. ``None`` when the best operation was used.
    better_accuracy_m: float | None = None
    missing_grids: list[str] = field(default_factory=list)
    #: True when the preferred operation needed a network grid that could not
    #: be fetched, and a less accurate local operation was used instead.
    network_fallback: bool = False


def _crs_or_raise(value: str | int) -> CRS:
    try:
        return CRS.from_user_input(value)
    except Exception as exc:
        raise GeoInputError.with_example(
            got=value,
            problem=f"Not a recognised coordinate reference system ({exc}).",
            example="EPSG:4326  (WGS84 lat/lon), EPSG:3857 (Web Mercator), EPSG:27700 (British National Grid)",
        ) from exc


def transform_point(
    x: float, y: float, source: str | int, target: str | int, *, always_xy: bool = True
) -> TransformResult:
    """Transform one coordinate pair between CRSs.

    With ``always_xy=True`` (the default) inputs and outputs are always
    ``(easting/longitude, northing/latitude)`` regardless of the axis order the
    CRS authority declares -- which is the source of most CRS bugs, because
    EPSG:4326 officially orders latitude first.
    """
    src, dst = _crs_or_raise(source), _crs_or_raise(target)
    transformer = Transformer.from_crs(src, dst, always_xy=always_xy)
    out_x, out_y = transformer.transform(x, y)
    operation = _last_operation(transformer, transformed=_finite(out_x, out_y))
    failed = None

    if not _finite(out_x, out_y) and pyproj.network.is_network_enabled():
        # With network grids enabled, PROJ may pick an operation whose grid it
        # then fails to download, and it reports that as infinities rather than
        # an error. Try the remaining candidates for this point in PROJ's own
        # order of preference before concluding anything about the point.
        fallback = _first_finite(src, dst, x, y, always_xy) if always_xy else None
        if fallback is not None:
            failed = operation
            out_x, out_y, operation = fallback

    if not _finite(out_x, out_y):
        raise GeoInputError.with_example(
            got=(x, y),
            problem=(
                f"The point falls outside the valid area of {dst.to_string()}. "
                "Projected CRSs are only defined for a specific region."
            ),
            example="for EPSG:27700 (British National Grid), a point within Great Britain",
        )

    description = str(getattr(operation, "description", "") or transformer.description or "")
    accuracy = getattr(operation, "accuracy", None)
    if accuracy is not None and accuracy < 0:
        accuracy = None
    if accuracy is None and (
        src == dst or (operation is not None and _is_conversion_only(operation))
    ):
        # The same CRS on both sides is the identity, and a change of projection
        # or axis order with no datum transformation is exact by definition.
        # PROJ 9.5 publishes 0 for most of these; PROJ 9.8 wraps the same steps
        # in an axis-order normalisation that publishes no accuracy.
        accuracy = 0.0
    # The diagnostics locate the point by longitude/latitude, which assumes x/y
    # axis order. Rather than guess at a lat-first caller's intent, skip them.
    better_accuracy, missing = (
        _better_unavailable(src, dst, x, y, out_x, out_y, accuracy) if always_xy else (None, [])
    )
    if failed is not None:
        # Network grids count as "available", so the grid that just failed to
        # download is not among the unavailable operations. Report it from the
        # operation PROJ tried first.
        failed_accuracy = getattr(failed, "accuracy", None)
        better_accuracy = failed_accuracy if failed_accuracy and failed_accuracy > 0 else None
        missing = _grids_of(failed)
    return TransformResult(
        x=out_x,
        y=out_y,
        source_epsg=src.to_string(),
        target_epsg=dst.to_string(),
        accuracy_m=accuracy,
        # PROJ names these operations "Ballpark geographic offset from X to Y":
        # no published transformation exists, so the datum shift is ignored.
        ballpark="ballpark" in description.lower(),
        pipeline=description or "unknown",
        better_accuracy_m=better_accuracy,
        missing_grids=missing,
        network_fallback=failed is not None,
    )


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _last_operation(transformer: Transformer, *, transformed: bool) -> object | None:
    """The operation PROJ used for the most recent point, or ``None``.

    ``transformed`` says whether that point came out finite. PROJ also raises
    when it has recorded no operation for a point that did go through: a
    transformer that resolved to a single no-op pipeline, such as EPSG:4326 to
    OGC:CRS84 (an axis swap) or EPSG:3857 to itself. That transformer is then
    the operation, with its own accuracy and steps. A transformer still choosing
    between candidates publishes accuracy -1 and no steps, so falling back to it
    never makes an unknown accuracy look exact.
    """
    try:
        return transformer.get_last_used_operation()
    except Exception:
        # With no finite point, PROJ raised because no operation ran: the point
        # was rejected.
        return transformer if transformed else None


def _is_conversion_only(operation: object) -> bool:
    """True when every step is a conversion, so no datum is changed.

    A conversion (a projection, its inverse, an axis swap, a unit change) is
    mathematically defined. Moving between datums takes a transformation step,
    and PROJ's ballpark offsets are transformation steps too, so an operation
    with even one of them is never treated as exact here.
    """
    steps = getattr(operation, "operations", None)
    if steps:
        return all(getattr(step, "type_name", None) == "Conversion" for step in steps)
    # A single-step operation lists no steps of its own; its JSON names its type.
    try:
        kind = operation.to_json_dict().get("type")  # type: ignore[attr-defined]
    except Exception:
        return False
    return bool(kind == "Conversion")


def _grids_of(operation: object) -> list[str]:
    """Grid files an operation uses.

    ``get_last_used_operation`` returns a Transformer, which has no ``grids`` of
    its own: they live on the CoordinateOperation steps it is made of.
    """
    steps = getattr(operation, "operations", None) or [operation]
    names = [g.short_name for step in steps for g in getattr(step, "grids", []) if g.short_name]
    return list(dict.fromkeys(names))


def _area_around(lon: float, lat: float) -> AreaOfInterest:
    pad = 0.01
    return AreaOfInterest(
        west_lon_degree=max(-180.0, lon - pad),
        south_lat_degree=max(-90.0, lat - pad),
        east_lon_degree=min(180.0, lon + pad),
        north_lat_degree=min(90.0, lat + pad),
    )


def _lonlat(crs: CRS, x: float, y: float) -> tuple[float, float] | None:
    """Longitude/latitude of a point, precise enough to choose operations by area.

    A projected CRS is inverted to its *own* geographic datum rather than to
    WGS84. That step is a pure inverse projection with no datum shift, so it
    needs no grid and cannot fail the way the transform being diagnosed just
    did. The result is within a few hundred metres of WGS84 anywhere, which is
    ample for deciding which operations' areas of use contain the point.
    """
    if crs.is_geographic:
        return x, y
    base = crs.geodetic_crs
    if base is None:
        return None
    try:
        lon, lat = Transformer.from_crs(crs, base, always_xy=True).transform(x, y)
    except Exception:
        return None
    return (lon, lat) if _finite(lon, lat) else None


def _group(src: CRS, dst: CRS, lonlat: tuple[float, float], always_xy: bool) -> TransformerGroup:
    # TransformerGroup warns when the best operation is unavailable. That is
    # exactly the case this module reports on, in the response, so the warning
    # would only duplicate it onto stderr.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return TransformerGroup(
            src, dst, always_xy=always_xy, area_of_interest=_area_around(*lonlat)
        )


def _first_finite(
    src: CRS, dst: CRS, x: float, y: float, always_xy: bool
) -> tuple[float, float, object] | None:
    lonlat = _lonlat(src, x, y)
    if lonlat is None:
        return None
    for candidate in _group(src, dst, lonlat, always_xy).transformers:
        try:
            cx, cy = candidate.transform(x, y)
        except Exception:
            continue
        if _finite(cx, cy):
            return cx, cy, candidate
    return None


def _better_unavailable(
    src: CRS,
    dst: CRS,
    x: float,
    y: float,
    out_x: float,
    out_y: float,
    used_accuracy: float | None,
) -> tuple[float | None, list[str]]:
    """The accuracy of a better operation this point could use, and its missing grids.

    Only operations valid for this point's area are considered, so the answer
    for a point in Scotland is not polluted by a grid that covers Spain.
    """
    if used_accuracy == 0:
        # A pure projection change with no datum shift: nothing can beat it.
        return None, []
    lonlat = _lonlat(src, x, y) if src.is_geographic or not dst.is_geographic else (out_x, out_y)
    if lonlat is None:
        return None, []
    try:
        group = _group(src, dst, lonlat, True)
    except Exception:
        return None, []
    best: tuple[float, list[str]] | None = None
    for op in group.unavailable_operations:
        accuracy = getattr(op, "accuracy", -1)
        if accuracy is None or accuracy <= 0:
            continue
        if used_accuracy is not None and accuracy >= used_accuracy:
            continue
        grids = [g.short_name for g in op.grids if not g.available and g.short_name]
        if grids and (best is None or accuracy < best[0]):
            best = (accuracy, grids)
    return best if best is not None else (None, [])


def utm_zone_of(lat: float, lon: float) -> tuple[int, str]:
    """Return the ``(zone_number, hemisphere)`` for a position.

    Handles the two documented irregularities in the UTM grid: zone 32V is
    widened over south-west Norway, and zones 31-37 are rearranged over
    Svalbard. Getting these wrong is a classic source of a several-hundred-
    kilometre error at high latitude.
    """
    hemisphere = "N" if lat >= 0 else "S"
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        return 32, hemisphere
    if 72.0 <= lat < 84.0:
        if 0.0 <= lon < 9.0:
            return 31, hemisphere
        if 9.0 <= lon < 21.0:
            return 33, hemisphere
        if 21.0 <= lon < 33.0:
            return 35, hemisphere
        if 33.0 <= lon < 42.0:
            return 37, hemisphere
    return int((lon + 180.0) / 6.0) % 60 + 1, hemisphere


def utm_crs_for(lat: float, lon: float) -> CRS:
    """The EPSG-registered UTM CRS covering a position.

    Used to give geometry operations a locally-accurate metric plane, so that
    buffers are circles on the ground rather than in degree space.

    Built on :func:`utm_zone_of` rather than PROJ's ``query_utm_crs_info``.
    That helper does a plain longitude division and is unaware of the zone 32V
    and Svalbard irregularities, so for Bergen (60N, 5E) it returns zone 31
    while :func:`utm_zone_of` returns the standard-correct zone 32. Deriving
    both from one place keeps them from disagreeing.
    """
    zone, hemisphere = utm_zone_of(lat, lon)
    return CRS.from_epsg((32600 if hemisphere == "N" else 32700) + zone)


def describe_crs(value: str | int) -> dict[str, object]:
    """Human-readable summary of a CRS, for the coordinate reference resource."""
    crs = _crs_or_raise(value)
    area = crs.area_of_use
    return {
        "epsg": crs.to_string(),
        "name": crs.name,
        "kind": "geographic"
        if crs.is_geographic
        else ("projected" if crs.is_projected else "other"),
        "units": [axis.unit_name for axis in crs.axis_info],
        "axis_order": [axis.abbrev for axis in crs.axis_info],
        "area_of_use": (
            {"name": area.name, "bounds": [area.west, area.south, area.east, area.north]}
            if area
            else None
        ),
    }
