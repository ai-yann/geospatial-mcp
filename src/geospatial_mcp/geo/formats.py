"""Conversion between geospatial data formats. Fully offline.

Format wrangling is the unglamorous majority of real geospatial work: a
customer sends a KML, the routing engine wants an encoded polyline, the
warehouse system exports CSV, and the web map needs GeoJSON. All of these are
handled here without GDAL.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

import polyline as polyline_codec
from shapely import from_wkb, from_wkt, to_wkb, to_wkt
from shapely.geometry import LineString, mapping, shape
from shapely.geometry.base import BaseGeometry

from ..errors import GeoInputError

__all__ = [
    "FORMATS",
    "decode_polyline",
    "encode_polyline",
    "from_any",
    "from_gpx",
    "from_kml",
    "points_from_csv",
    "to_geojson",
    "to_gpx",
    "to_kml",
    "to_wkt_text",
    "validate_geojson",
]

#: Formats ``data_convert`` accepts as input and can emit as output.
FORMATS = ("geojson", "wkt", "wkb", "polyline", "kml", "gpx", "csv")

_KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def from_any(data: str, fmt: str = "auto") -> BaseGeometry:
    """Parse a geometry from any supported textual format."""
    text = data.strip()
    if fmt == "auto":
        fmt = _sniff(text)
    if fmt == "geojson":
        obj = json.loads(text)
        if obj.get("type") == "FeatureCollection":
            from shapely import unary_union

            geoms = [shape(f["geometry"]) for f in obj.get("features", []) if f.get("geometry")]
            if not geoms:
                raise GeoInputError("The FeatureCollection contains no geometries.")
            return unary_union(geoms)
        if obj.get("type") == "Feature":
            obj = obj["geometry"]
        return shape(obj)
    if fmt == "wkt":
        return from_wkt(text)
    if fmt == "wkb":
        return from_wkb(bytes.fromhex(text))
    if fmt == "polyline":
        return LineString([(lon, lat) for lat, lon in decode_polyline(text)])
    if fmt == "kml":
        return from_kml(text)
    if fmt == "gpx":
        return from_gpx(text)
    raise GeoInputError.with_example(
        got=fmt,
        problem=f"Unsupported format. Valid: {', '.join(FORMATS)}, or 'auto'.",
        example="geojson",
    )


def _sniff(text: str) -> str:
    """Guess the format of a payload from its first characters."""
    head = text.lstrip()[:200].lower()
    if head.startswith("{"):
        return "geojson"
    if "<kml" in head or ("<?xml" in head and "kml" in text[:400].lower()):
        return "kml"
    if "<gpx" in head or ("<?xml" in head and "gpx" in text[:400].lower()):
        return "gpx"
    if head.startswith(
        (
            "point",
            "linestring",
            "polygon",
            "multipoint",
            "multilinestring",
            "multipolygon",
            "geometrycollection",
        )
    ):
        return "wkt"
    try:
        bytes.fromhex(text.strip())
        return "wkb"
    except ValueError:
        pass
    return "polyline"


def to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    """GeoJSON geometry dict for a Shapely geometry."""
    return mapping(geom)


def to_wkt_text(geom: BaseGeometry, *, rounding_precision: int = -1) -> str:
    return to_wkt(geom, rounding_precision=rounding_precision)


def to_wkb_hex(geom: BaseGeometry) -> str:
    return to_wkb(geom, hex=True)


def encode_polyline(coords: Sequence[tuple[float, float]], precision: int = 5) -> str:
    """Encode ``(lat, lon)`` pairs as a Google encoded polyline.

    Roughly ten times more compact than the equivalent GeoJSON, which is why it
    is the default geometry encoding for routes: a 500-point route is a few
    hundred bytes rather than several kilobytes of an agent's context.
    """
    return polyline_codec.encode(list(coords), precision)


def decode_polyline(value: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode an encoded polyline to ``(lat, lon)`` pairs."""
    try:
        return polyline_codec.decode(value, precision)
    except Exception as exc:
        raise GeoInputError.with_example(
            got=value[:40],
            problem=f"Not a valid encoded polyline ({exc}).",
            example="_p~iF~ps|U_ulLnnqC",
        ) from exc


def to_kml(geom: BaseGeometry, *, name: str = "geometry") -> str:
    """Serialise a geometry to KML for Google Earth."""

    def coords_of(g: BaseGeometry) -> str:
        return " ".join(f"{x},{y},0" for x, y in g.coords)

    if geom.geom_type == "Point":
        body = f"<Point><coordinates>{geom.x},{geom.y},0</coordinates></Point>"
    elif geom.geom_type == "LineString":
        body = f"<LineString><coordinates>{coords_of(geom)}</coordinates></LineString>"
    elif geom.geom_type == "Polygon":
        body = (
            "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{coords_of(geom.exterior)}"
            "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        )
    else:
        parts = "".join(
            to_kml(g, name=name).split("<Placemark>")[1].split("</Placemark>")[0]
            for g in geom.geoms
        )
        body = f"<MultiGeometry>{parts}</MultiGeometry>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<Placemark><name>{name}</name>{body}</Placemark>"
        "</Document></kml>"
    )


def from_kml(text: str) -> BaseGeometry:
    """Parse the first geometry out of a KML document."""
    from shapely import unary_union
    from shapely.geometry import Point, Polygon

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeoInputError.with_example(
            got=text[:60],
            problem=f"Not well-formed KML XML ({exc}).",
            example="<kml><Document><Placemark><Point><coordinates>2.29,48.86,0</coordinates></Point></Placemark></Document></kml>",
        ) from exc

    geoms: list[BaseGeometry] = []
    for tag, builder in (
        ("Point", lambda pts: Point(pts[0])),
        ("LineString", LineString),
        ("Polygon", lambda pts: Polygon(pts)),
    ):
        for node in root.iter():
            if not node.tag.endswith(tag):
                continue
            coord_node = next((c for c in node.iter() if c.tag.endswith("coordinates")), None)
            if coord_node is None or not coord_node.text:
                continue
            points = _parse_kml_coords(coord_node.text)
            if points:
                geoms.append(builder(points))
    if not geoms:
        raise GeoInputError("No Point, LineString or Polygon geometry found in that KML.")
    return geoms[0] if len(geoms) == 1 else unary_union(geoms)


def _parse_kml_coords(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in text.replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return points


def to_gpx(coords: Sequence[tuple[float, float]], *, name: str = "track") -> str:
    """Serialise ``(lat, lon)`` pairs as a GPX 1.1 track."""
    points = "".join(f'<trkpt lat="{lat}" lon="{lon}"></trkpt>' for lat, lon in coords)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="mcp-geospatial" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name><trkseg>{points}</trkseg></trk></gpx>"
    )


def from_gpx(text: str) -> BaseGeometry:
    """Parse GPX track or route points into a LineString."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeoInputError.with_example(
            got=text[:60],
            problem=f"Not well-formed GPX XML ({exc}).",
            example='<gpx version="1.1"><trk><trkseg><trkpt lat="48.86" lon="2.29"/></trkseg></trk></gpx>',
        ) from exc
    points = [
        (float(node.get("lon", "nan")), float(node.get("lat", "nan")))
        for node in root.iter()
        if node.tag.endswith(("trkpt", "rtept", "wpt")) and node.get("lat") and node.get("lon")
    ]
    if not points:
        raise GeoInputError("No track, route or waypoint points found in that GPX.")
    if len(points) == 1:
        from shapely.geometry import Point

        return Point(points[0])
    return LineString(points)


def points_from_csv(
    text: str, *, lat_field: str | None = None, lon_field: str | None = None
) -> list[dict[str, Any]]:
    """Read points from CSV, auto-detecting the coordinate columns.

    Recognises the common spellings so callers do not have to specify them:
    lat/latitude/y, lon/lng/long/longitude/x.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise GeoInputError("The CSV has no header row, so coordinate columns cannot be found.")
    lowered = {name.lower().strip(): name for name in reader.fieldnames}
    lat_key = lat_field or next(
        (lowered[c] for c in ("latitude", "lat", "y") if c in lowered), None
    )
    lon_key = lon_field or next(
        (lowered[c] for c in ("longitude", "lon", "lng", "long", "x") if c in lowered), None
    )
    if not lat_key or not lon_key:
        raise GeoInputError.with_example(
            got=reader.fieldnames,
            problem="Could not find latitude and longitude columns in the CSV header.",
            example="a header row of: name,latitude,longitude",
        )
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader, start=2):
        try:
            lat, lon = float(row[lat_key]), float(row[lon_key])
        except (TypeError, ValueError):
            continue  # skip unparseable rows rather than failing the whole file
        rows.append(
            {
                "latitude": lat,
                "longitude": lon,
                "row": index,
                "properties": {k: v for k, v in row.items() if k not in {lat_key, lon_key}},
            }
        )
    if not rows:
        raise GeoInputError(
            f"No rows had parseable numbers in columns {lat_key!r} and {lon_key!r}."
        )
    return rows


def validate_geojson(obj: str | dict[str, Any]) -> dict[str, Any]:
    """Check a GeoJSON document against RFC 7946 and report specific problems."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError as exc:
            return {"valid": False, "errors": [f"Not valid JSON: {exc}"]}
    if not isinstance(obj, dict):
        return {
            "valid": False,
            "errors": [f"GeoJSON must be an object, got {type(obj).__name__}."],
        }
    document: dict[str, Any] = obj
    errors: list[str] = []
    warnings: list[str] = []
    kind = document.get("type")
    if not kind:
        return {"valid": False, "errors": ["Missing the required 'type' member."]}

    geometries: list[dict[str, Any]] = []
    if kind == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list):
            errors.append("A FeatureCollection needs a 'features' array.")
        else:
            geometries = [
                feature["geometry"]
                for feature in features
                if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
            ]
    elif kind == "Feature":
        geometry = document.get("geometry")
        if isinstance(geometry, dict):
            geometries = [geometry]
    else:
        geometries = [document]

    for geometry in geometries:
        try:
            geom = shape(geometry)
        except Exception as exc:
            errors.append(f"Invalid geometry: {exc}")
            continue
        if not geom.is_valid:
            from shapely.validation import explain_validity

            errors.append(f"Geometry is not valid: {explain_validity(geom)}")
        west, south, east, north = geom.bounds if not geom.is_empty else (0, 0, 0, 0)
        if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
            errors.append(f"Longitude out of range in bounds {geom.bounds}.")
        if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
            errors.append(
                f"Latitude out of range in bounds {geom.bounds}. "
                "RFC 7946 orders each position as [longitude, latitude] -- the values "
                "are most likely transposed."
            )
        # Note on what this cannot catch: a transposition where both values
        # happen to stay in range -- [48.8584, 2.2945] for Paris -- is
        # structurally valid GeoJSON that simply denotes somewhere else. A
        # heuristic on "longitude > 90" was tried and rejected: it fires across
        # Australia, East Asia and the Pacific, so it costs more in false
        # positives than it recovers in caught mistakes.
    if "crs" in document:
        warnings.append(
            "RFC 7946 removed the 'crs' member; GeoJSON is always WGS84 (CRS84). "
            "The member will be ignored by conforming readers."
        )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "type": kind,
        "geometry_count": len(geometries),
    }
