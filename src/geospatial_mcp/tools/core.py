"""The core toolset: 20 general-purpose geospatial tools.

Tool descriptions are prompt surface. Each one leads with what the tool does,
names the situations it is the right choice for, and -- where a neighbouring
tool exists -- says explicitly when to use that one instead. Tool-selection
accuracy depends more on these paragraphs than on anything else in the package.
"""

# NOTE: deliberately no `from __future__ import annotations` here.
# Response models are defined inside register(), and PEP 563 string
# annotations cannot be resolved from a function-local scope -- the SDK
# raises InvalidSignature. Eager evaluation resolves them correctly.

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from .._sdk import MCPServer, compute, network
from ..errors import GeoInputError
from ..geo import formats as fmt
from ..geo import geodesy, grid_ref, grids, solar, timezones
from ..geo.coords import Location, format_dms, parse_location
from ..geo.crs import TransformResult, describe_crs, transform_point, utm_zone_of
from ..geo.geometry import (
    geodesic_buffer,
    geometry_from_geojson,
    geometry_to_geojson,
    hull,
    measure_geometry,
    overlay,
    repair,
    simplify_geometry,
)
from ..geo.terrain import terrain_statistics
from ..models import GeoModel, ResponseMeta
from ._shared import (
    MENTION_PROMINENCE,
    GeometryInput,
    as_geometry_text,
    clamp,
    far_alternatives,
    meta,
    require_metres,
    resolve,
    resolve_pair,
)

__all__ = ["register"]


# ---------------------------------------------------------------- coordinates
class CoordFormats(GeoModel):
    latitude: float
    longitude: float
    decimal: str
    dms: str
    grid_reference: str | None = None
    utm: str | None = None
    geohash: str | None = None
    h3: str | None = None
    plus_code: str | None = None
    quadkey: str | None = None
    geojson: dict[str, Any]


class CoordConvertResult(GeoModel):
    input: str
    detected_format: str
    input_precision_m: float | None = None
    formats: CoordFormats
    meta: ResponseMeta


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=compute())
    def coord_convert(
        location: Annotated[
            str,
            Field(
                description=(
                    "A position in any supported notation: decimal degrees "
                    "'48.8584,2.2945'; DMS '48°51'30\"N 2°17'40\"E'; USNG/MGRS "
                    "'31UDQ4825211954'; geohash 'u09tunquc'; H3 '891fb46741bffff'; "
                    "Plus Code '8FW4V75V+9R6'; or a place name to geocode."
                )
            ),
        ],
        grid_digits: Annotated[
            int,
            Field(
                ge=2,
                le=10,
                description=(
                    "Digits in the USNG/MGRS reference: 10 gives 1 m, 8 gives 10 m, 4 gives 1 km."
                ),
            ),
        ] = 10,
        geohash_length: Annotated[int, Field(ge=1, le=12, description="Geohash characters.")] = 9,
        h3_resolution: Annotated[int, Field(ge=0, le=15, description="H3 resolution.")] = 9,
        tile_zoom: Annotated[
            int, Field(ge=0, le=22, description="Web-map zoom for the quadkey.")
        ] = 16,
    ) -> CoordConvertResult:
        """Convert a position between every common coordinate notation at once.

        Detects the input format automatically and returns decimal degrees, DMS,
        USNG/MGRS, UTM, geohash, H3, Plus Code, quadkey and GeoJSON together, so
        one call answers any "what is this in X?" question.

        Use this for format conversion. For a different *datum or projection*
        (British National Grid, State Plane, Web Mercator) use coord_transform_crs.
        To check whether a string is a valid coordinate without converting it,
        use coord_describe.
        """
        parsed = parse_location(location)
        used_geocoding = not parsed.resolved
        loc = resolve(location)
        lat, lon = loc.as_tuple()

        def safe(fn, *args):
            """Some grid systems are undefined outside their design envelope."""
            try:
                return fn(*args)
            except Exception:
                return None

        formats = CoordFormats(
            latitude=round(lat, 8),
            longitude=round(lon, 8),
            decimal=f"{lat:.6f}, {lon:.6f}",
            dms=f"{format_dms(lat, is_latitude=True)} {format_dms(lon, is_latitude=False)}",
            grid_reference=safe(grid_ref.from_latlon, lat, lon, grid_digits),
            utm=_utm_string(lat, lon),
            geohash=safe(grids.geohash_encode, lat, lon, geohash_length),
            h3=safe(grids.h3_encode, lat, lon, h3_resolution),
            plus_code=safe(grids.pluscode_encode, lat, lon, 11),
            quadkey=safe(grids.quadkey_of, lat, lon, tile_zoom),
            geojson={"type": "Point", "coordinates": [round(lon, 8), round(lat, 8)]},
        )
        notes: list[str] = []
        if parsed.kind == "grid":
            notes.append(
                "A USNG/MGRS reference names the south-west corner of a grid square, "
                f"not a point; this one is {parsed.precision_m:.0f} m on a side."
            )
        return CoordConvertResult(
            input=location,
            detected_format="place name (geocoded)" if used_geocoding else parsed.kind,
            input_precision_m=parsed.precision_m,
            formats=formats,
            meta=meta(offline=True, notes=notes, resolved=[loc]),
        )

    # ------------------------------------------------------------------------
    class CoordDescribeResult(GeoModel):
        input: str
        is_valid: bool
        detected_format: str | None = None
        latitude: float | None = None
        longitude: float | None = None
        implied_precision_m: float | None = None
        normalized: str | None = None
        problem: str | None = None
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def coord_describe(
        value: Annotated[str, Field(description="The coordinate string to inspect.")],
    ) -> CoordDescribeResult:
        """Check whether a string is a valid coordinate and report its precision.

        Identifies which notation it is, normalises it, and states the ground
        accuracy the notation implies -- a grid reference with four digits names
        a 1 km square, not a point, and a 5-character geohash is about 5 km wide.
        Returns is_valid=false with an explanation rather than failing, so it is
        safe to use for validating user input before acting on it.
        """
        try:
            parsed = parse_location(value)
        except GeoInputError as exc:
            return CoordDescribeResult(
                input=value,
                is_valid=False,
                problem=str(exc),
                meta=meta(offline=True),
            )
        if parsed.kind == "place_name":
            return CoordDescribeResult(
                input=value,
                is_valid=False,
                detected_format="place_name",
                problem=(
                    "This is free text, not a coordinate. It can still be used as a "
                    "location anywhere in this server -- it will be geocoded."
                ),
                meta=meta(offline=True),
            )
        return CoordDescribeResult(
            input=value,
            is_valid=True,
            detected_format=parsed.kind,
            latitude=parsed.latitude,
            longitude=parsed.longitude,
            implied_precision_m=parsed.precision_m,
            normalized=(
                grid_ref.normalize(value)
                if parsed.kind == "grid"
                else f"{parsed.latitude:.6f}, {parsed.longitude:.6f}"
            ),
            meta=meta(offline=True),
        )

    # ------------------------------------------------------------------------
    class CrsTransformResult(GeoModel):
        x: float
        y: float
        source: str
        target: str
        accuracy_m: float | None
        ballpark: bool
        pipeline: str
        missing_grids: list[str] = []
        source_info: dict[str, Any]
        target_info: dict[str, Any]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def coord_transform_crs(
        x: Annotated[float, Field(description="Easting or longitude, in the source CRS units.")],
        y: Annotated[float, Field(description="Northing or latitude, in the source CRS units.")],
        source_crs: Annotated[
            str, Field(description="Source CRS, e.g. 'EPSG:4326'.")
        ] = "EPSG:4326",
        target_crs: Annotated[
            str, Field(description="Target CRS, e.g. 'EPSG:27700'.")
        ] = "EPSG:3857",
    ) -> CrsTransformResult:
        """Transform coordinates between any two coordinate reference systems.

        For national grids (EPSG:27700 British National Grid, EPSG:2154 Lambert-93,
        State Plane), Web Mercator (EPSG:3857) and any other EPSG-registered CRS.

        Reports the transformation PROJ used for this point and its published
        accuracy. ballpark=true means no published transformation links the two
        datums, so the shift was ignored: expect tens to hundreds of metres of
        error. When a more accurate transformation needs a grid file that is not
        installed, missing_grids names it.

        Inputs and outputs are always x/easting first, y/northing second,
        regardless of the axis order the CRS authority declares.
        """
        result = transform_point(x, y, source_crs, target_crs)
        notes = _transform_notes(result)
        return CrsTransformResult(
            x=result.x,
            y=result.y,
            source=result.source_epsg,
            target=result.target_epsg,
            accuracy_m=result.accuracy_m,
            ballpark=result.ballpark,
            pipeline=result.pipeline,
            missing_grids=result.missing_grids,
            source_info=describe_crs(source_crs),
            target_info=describe_crs(target_crs),
            meta=meta(offline=True, notes=notes),
        )

    _register_geometry(mcp)
    _register_places(mcp)
    _register_routing(mcp)
    _register_terrain(mcp)
    _register_environment(mcp)
    _register_data(mcp)
    _register_capabilities(mcp)


def _transform_notes(result: TransformResult) -> list[str]:
    """Say what the accuracy figures mean, in the terms a user can act on."""
    notes: list[str] = []
    used = f"'{result.pipeline}'"
    if result.accuracy_m is not None:
        used += f", accurate to about {result.accuracy_m:g} m"
    if result.ballpark:
        notes.append(
            "PROJ has no published transformation between these datums, so it used a "
            "ballpark offset that ignores the datum shift entirely. Expect errors of "
            "tens to hundreds of metres."
        )
    grids = ", ".join(result.missing_grids)
    better = f"{result.better_accuracy_m:g} m" if result.better_accuracy_m else "more accurate"
    if result.network_fallback and grids:
        notes.append(
            f"PROJ_NETWORK is on, but {grids} could not be fetched from cdn.proj.org. "
            f"Fell back to {used}; the {better} transformation needs that grid."
        )
    elif grids:
        notes.append(
            f"Used {used}. A {better} transformation exists for this point but needs "
            f"{grids}. Set PROJ_NETWORK=ON to fetch grids on demand, or install them "
            "with projsync."
        )
    return notes


def _utm_string(lat: float, lon: float) -> str | None:
    """Format a position as a UTM zone, easting and northing."""
    try:
        from ..geo.crs import utm_crs_for

        zone, hemisphere = utm_zone_of(lat, lon)
        crs = utm_crs_for(lat, lon)
        result = transform_point(lon, lat, "EPSG:4326", crs.to_string())
        return f"{zone}{hemisphere} {result.x:.1f}E {result.y:.1f}N"
    except Exception:
        return None


# ------------------------------------------------------------------- geometry
def _register_geometry(mcp: MCPServer) -> None:
    class MeasureResult(GeoModel):
        distance_m: float | None = None
        distance_km: float | None = None
        distance_miles: float | None = None
        distance_nautical_miles: float | None = None
        initial_bearing_deg: float | None = None
        final_bearing_deg: float | None = None
        compass: str | None = None
        midpoint: dict[str, float] | None = None
        geometry: dict[str, Any] | None = None
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def geom_measure(
        origin: Annotated[
            str, Field(description="Start position, in any coordinate notation or as a place name.")
        ],
        destination: Annotated[
            str | None,
            Field(
                description=(
                    "End position. Omit when measuring a geometry supplied in `geojson` instead."
                )
            ),
        ] = None,
        geojson: Annotated[
            GeometryInput | None,
            Field(
                description=(
                    "A GeoJSON or WKT geometry to measure instead of a point pair. "
                    "Accepts a JSON object or a string. Returns geodesic area, "
                    "perimeter, length, centroid and bounding box."
                )
            ),
        ] = None,
    ) -> MeasureResult:
        """Measure geodesic distance and bearing between points, or a geometry's area.

        Distances use Karney's algorithm on the WGS84 ellipsoid, accurate to
        millimetres at any distance including near-antipodal pairs. Areas are
        computed on the ellipsoid, not in degree space.

        This is straight-line ("as the crow flies") distance. For travel distance
        and time along real roads, use route_directions instead -- road distance is
        typically 20-40% longer.
        """
        geojson_text = as_geometry_text(geojson)
        if geojson_text:
            geom = geometry_from_geojson(geojson_text)
            measurements = measure_geometry(geom)
            return MeasureResult(
                geometry=measurements,
                meta=meta(offline=True),
            )
        if not destination:
            raise GeoInputError.with_example(
                got=None,
                problem="Provide either a destination to measure to, or a geojson geometry to measure.",
                example="origin='48.8584,2.2945', destination='51.5007,-0.1246'",
            )
        start, end = resolve_pair(origin, destination, names=("origin", "destination"))
        lat1, lon1 = start.as_tuple()
        lat2, lon2 = end.as_tuple()
        forward, backward, distance = geodesy.inverse(lat1, lon1, lat2, lon2)
        mid_lat, mid_lon = geodesy.midpoint(lat1, lon1, lat2, lon2)
        return MeasureResult(
            distance_m=round(distance, 3),
            distance_km=round(distance / 1000.0, 4),
            distance_miles=round(distance / 1609.344, 4),
            distance_nautical_miles=round(distance / 1852.0, 4),
            initial_bearing_deg=round(forward, 4),
            final_bearing_deg=round(backward, 4),
            compass=geodesy.compass_point(forward),
            midpoint={"latitude": round(mid_lat, 8), "longitude": round(mid_lon, 8)},
            meta=meta(offline=True, resolved=[start, end]),
        )

    class GeometryResult(GeoModel):
        geometry: dict[str, Any]
        measurements: dict[str, Any]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def geom_transform(
        geojson: Annotated[
            GeometryInput, Field(description="Input geometry as GeoJSON (object or string) or WKT.")
        ],
        operation: Annotated[
            Literal[
                "buffer", "simplify", "convex_hull", "concave_hull", "centroid", "repair", "bbox"
            ],
            Field(description="The transformation to apply."),
        ],
        distance_m: Annotated[
            float,
            Field(
                description=(
                    "For buffer: offset distance in metres (negative shrinks). "
                    "For simplify: tolerance in metres."
                )
            ),
        ] = 0.0,
        ratio: Annotated[
            float,
            Field(
                ge=0.0,
                le=1.0,
                description=(
                    "For concave_hull: 0 hugs the points tightly, 1 equals the convex hull. "
                    "The scale is steeply non-linear -- try 0.0 first and raise it only if "
                    "the outline is too ragged."
                ),
            ),
        ] = 0.0,
    ) -> GeometryResult:
        """Reshape a geometry: buffer, simplify, hull, centroid, repair or bounding box.

        All distances are true ground metres. Buffering projects to an azimuthal
        equidistant plane centred on the geometry, so a 5 km buffer is 5 km on the
        ground at every latitude -- not an ellipse stretched by longitude
        convergence, which is what buffering in degrees produces.

        concave_hull traces the real outline of a scattered point set (a service
        territory, a fire perimeter); convex_hull bridges across genuine gaps.
        """
        geom = geometry_from_geojson(as_geometry_text(geojson) or "")
        if operation == "buffer":
            if distance_m == 0:
                raise GeoInputError.with_example(
                    got=distance_m,
                    problem="A buffer needs a non-zero distance in metres.",
                    example="1000  (a 1 km buffer)",
                )
            require_metres(distance_m, name="distance_m", example="7500  (a 7.5 km buffer)")
            result = geodesic_buffer(geom, distance_m)
        elif operation == "simplify":
            result = simplify_geometry(geom, distance_m or 10.0)
        elif operation == "convex_hull":
            result = hull(geom, kind="convex")
        elif operation == "concave_hull":
            result = hull(geom, kind="concave", ratio=ratio)
        elif operation == "centroid":
            result = geom.centroid
        elif operation == "repair":
            result = repair(geom)
        else:  # bbox
            from shapely.geometry import box

            result = box(*geom.bounds)
        return GeometryResult(
            geometry=geometry_to_geojson(result),
            measurements=measure_geometry(result),
            meta=meta(offline=True),
        )

    @mcp.tool(annotations=compute())
    def geom_overlay(
        geojson_a: Annotated[
            GeometryInput,
            Field(description="First geometry, as GeoJSON (object or string) or WKT."),
        ],
        geojson_b: Annotated[
            GeometryInput,
            Field(description="Second geometry, as GeoJSON (object or string) or WKT."),
        ],
        operation: Annotated[
            Literal["intersection", "union", "difference", "symmetric_difference"],
            Field(description="The set operation to apply."),
        ] = "intersection",
    ) -> GeometryResult:
        """Combine two geometries with a set operation, reporting the resulting area.

        The workhorse of overlay analysis: how much of this delivery zone falls
        inside that flood extent (intersection), what is the combined footprint of
        these service areas (union), which part of a territory is not yet covered
        (difference).

        Invalid input geometries are repaired automatically before the operation,
        since self-intersecting polygons are common in real data.
        """
        left = geometry_from_geojson(as_geometry_text(geojson_a) or "")
        right = geometry_from_geojson(as_geometry_text(geojson_b) or "")
        result = overlay(left, right, operation)
        measurements = measure_geometry(result)
        left_area = measure_geometry(left).get("area_m2")
        if operation == "intersection" and left_area:
            measurements["percent_of_first_geometry"] = round(
                100.0 * measurements.get("area_m2", 0.0) / left_area, 4
            )
        return GeometryResult(
            geometry=geometry_to_geojson(result),
            measurements=measurements,
            meta=meta(offline=True),
        )

    class RelateResult(GeoModel):
        relationships: dict[str, bool]
        distance_m: float
        nearest_points: list[dict[str, float]]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def geom_relate(
        geojson_a: Annotated[
            GeometryInput,
            Field(description="First geometry, as GeoJSON (object or string) or WKT."),
        ],
        geojson_b: Annotated[
            GeometryInput,
            Field(description="Second geometry, as GeoJSON (object or string) or WKT."),
        ],
    ) -> RelateResult:
        """Test the spatial relationship between two geometries.

        Answers point-in-polygon, overlap, containment and adjacency in one call,
        plus the shortest geodesic distance between them and the nearest point on
        each. Use this for geofence checks ("is this vehicle inside the zone?"),
        territory assignment and proximity screening.
        """
        left = geometry_from_geojson(as_geometry_text(geojson_a) or "")
        right = geometry_from_geojson(as_geometry_text(geojson_b) or "")
        from shapely.ops import nearest_points

        near_a, near_b = nearest_points(left, right)
        return RelateResult(
            relationships={
                "intersects": left.intersects(right),
                "contains": left.contains(right),
                "within": left.within(right),
                "touches": left.touches(right),
                "crosses": left.crosses(right),
                "overlaps": left.overlaps(right),
                "disjoint": left.disjoint(right),
                "covers": left.covers(right),
            },
            distance_m=round(geodesy.distance_m(near_a.y, near_a.x, near_b.y, near_b.x), 3),
            nearest_points=[
                {"latitude": round(near_a.y, 8), "longitude": round(near_a.x, 8)},
                {"latitude": round(near_b.y, 8), "longitude": round(near_b.x, 8)},
            ],
            meta=meta(offline=True),
        )


# --------------------------------------------------------------------- places
def _register_places(mcp: MCPServer) -> None:
    class GeocodeMatch(GeoModel):
        latitude: float
        longitude: float
        display_name: str
        category: str | None = None
        type: str | None = None
        address: dict[str, str] = {}
        bbox: list[float] | None = None
        grid_reference: str | None = None

    class GeocodeResult(GeoModel):
        query: str
        provider: str
        matches: list[GeocodeMatch]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def place_geocode(
        query: Annotated[str, Field(description="An address, place name or landmark.")],
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum matches to return.")] = 1,
        country_codes: Annotated[
            str | None,
            Field(
                description=(
                    "Comma-separated ISO 3166-1 alpha-2 codes to restrict results, e.g. 'gb,ie'."
                )
            ),
        ] = None,
        fuzzy: Annotated[
            bool,
            Field(
                description=(
                    "Use the typo-tolerant Photon backend instead of Nominatim. Set this for "
                    "partial, misspelled or as-you-type input."
                )
            ),
        ] = False,
    ) -> GeocodeResult:
        """Find the coordinates of an address, place name or landmark.

        Uses OpenStreetMap: Nominatim for exact structured lookups, or Photon when
        fuzzy=true for misspelled or partial input (Nominatim's usage policy
        forbids autocomplete-style queries, so route those here).

        To go the other way -- coordinates to an address -- use place_reverse.
        To find *categories* of thing nearby (fuel, hospitals, hotels) rather than
        one named place, use place_search.
        """
        from ..net.providers import nominatim, photon

        notes: list[str] = []
        if fuzzy:
            raw, cached = photon.search(query, limit=limit)
            matches = [
                GeocodeMatch(
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    display_name=r.get("name") or query,
                    type=r.get("type"),
                    address=r.get("address", {}),
                    grid_reference=_safe_grid(r["latitude"], r["longitude"]),
                )
                for r in raw
            ]
            source = "photon"
        else:
            # Always fetch the shared candidate count so this call and every tool's
            # own geocoding hit one cache entry, then return what was asked for.
            candidates, cached = nominatim.geocode(
                query, limit=max(limit, nominatim.CANDIDATES), country_codes=country_codes
            )
            raw = candidates[:limit]
            hidden = [
                r for r in far_alternatives(candidates, within=MENTION_PROMINENCE) if r not in raw
            ]
            if hidden:
                notes.append(
                    "Other places share this name with similar prominence: "
                    + "; ".join(r["display_name"] for r in hidden[:3])
                    + f". Pass limit={nominatim.CANDIDATES} to compare them."
                )
            matches = [
                GeocodeMatch(
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    display_name=r["display_name"],
                    category=r.get("category"),
                    type=r.get("type"),
                    address=r.get("address", {}),
                    bbox=r.get("bbox"),
                    grid_reference=_safe_grid(r["latitude"], r["longitude"]),
                )
                for r in raw
            ]
            source = "nominatim"
        return GeocodeResult(
            query=query,
            provider=source,
            matches=matches,
            meta=meta(source, cached=cached, notes=notes),
        )

    class ReverseResult(GeoModel):
        latitude: float
        longitude: float
        display_name: str
        address: dict[str, str]
        category: str | None = None
        type: str | None = None
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def place_reverse(
        location: Annotated[
            str,
            Field(
                description=(
                    "Position in any coordinate notation: decimal degrees, DMS, "
                    "a USNG/MGRS grid reference, geohash, H3 or Plus Code."
                )
            ),
        ],
        zoom: Annotated[
            int,
            Field(
                ge=3,
                le=18,
                description=("Detail level: 18 building, 16 street, 10 city, 5 state, 3 country."),
            ),
        ] = 18,
    ) -> ReverseResult:
        """Find the address or place name at a set of coordinates.

        The inverse of place_geocode. Useful for labelling GPS traces, describing
        a delivery destination, or identifying the administrative area a position
        falls in (lower the zoom to get city, region or country instead of a
        street address).
        """
        from ..net.providers import nominatim

        loc = resolve(location, allow_geocode=False)
        lat, lon = loc.as_tuple()
        raw, cached = nominatim.reverse(lat, lon, zoom=zoom)
        return ReverseResult(
            latitude=lat,
            longitude=lon,
            display_name=raw["display_name"],
            address=raw["address"],
            category=raw.get("category"),
            type=raw.get("type"),
            meta=meta("nominatim", cached=cached),
        )

    class Place(GeoModel):
        name: str | None = None
        latitude: float
        longitude: float
        distance_m: float | None = None
        bearing: str | None = None
        tags: dict[str, str] = {}

    class PlaceSearchResult(GeoModel):
        category: str
        centre: dict[str, float]
        radius_m: float
        count: int
        places: list[Place]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def place_search(
        location: Annotated[
            str, Field(description="Centre of the search, as coordinates or a place name.")
        ],
        category: Annotated[
            str,
            Field(
                description=(
                    "What to look for. One of: fuel, ev_charging, parking, hospital, pharmacy, "
                    "clinic, police, fire_station, school, restaurant, cafe, hotel, supermarket, "
                    "bank, atm, toilets, drinking_water, bus_stop, railway_station, airport, port, "
                    "warehouse, power_substation, power_tower, communication_tower, water_tower, "
                    "viewpoint, shelter, campsite, peak, bridge, farmland."
                )
            ),
        ],
        radius_m: Annotated[
            float, Field(gt=0, le=50000, description="Search radius in metres.")
        ] = 2000,
        limit: Annotated[int, Field(ge=1, le=200, description="Maximum results.")] = 25,
    ) -> PlaceSearchResult:
        """Find points of interest of a given category near a location.

        Searches OpenStreetMap for facilities by type -- fuel stations along a
        route, hospitals near an incident, EV chargers in a city, warehouses in a
        catchment, communication towers on a ridge. Results are sorted by distance
        and include the bearing from the search centre.

        For one specific named place, use place_geocode instead.
        """
        from ..net.providers import overpass

        require_metres(radius_m, name="radius_m", example="2000  (a 2 km search radius)")
        loc = resolve(location)
        lat, lon = loc.as_tuple()
        raw, cached = overpass.around(lat, lon, radius_m, category, limit=limit)
        places = []
        for item in raw:
            distance = geodesy.distance_m(lat, lon, item["latitude"], item["longitude"])
            bearing = geodesy.bearing_deg(lat, lon, item["latitude"], item["longitude"])
            places.append(
                Place(
                    name=item.get("name"),
                    latitude=item["latitude"],
                    longitude=item["longitude"],
                    distance_m=round(distance, 1),
                    bearing=geodesy.compass_point(bearing),
                    tags=item.get("tags", {}),
                )
            )
        places.sort(key=lambda p: p.distance_m or 0.0)
        return PlaceSearchResult(
            category=category,
            centre={"latitude": round(lat, 6), "longitude": round(lon, 6)},
            radius_m=radius_m,
            count=len(places),
            places=places,
            meta=meta("overpass", cached=cached, truncated=len(places) >= limit, resolved=[loc]),
        )


def _safe_grid(lat: float, lon: float) -> str | None:
    try:
        return grid_ref.from_latlon(lat, lon, 10)
    except Exception:
        return None


# -------------------------------------------------------------------- routing
def _register_routing(mcp: MCPServer) -> None:
    class RouteStep(GeoModel):
        instruction: str
        distance_m: float
        duration_s: float
        latitude: float
        longitude: float
        street: str | None = None

    class RouteResult(GeoModel):
        profile: str
        profile_description: str
        distance_km: float
        distance_miles: float
        duration_s: float
        duration_display: str
        start: dict[str, Any]
        end: dict[str, Any]
        steps: list[RouteStep] = []
        geometry_polyline: str | None = None
        geometry_geojson: dict[str, Any] | None = None
        provider: str
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def route_directions(
        origin: Annotated[str, Field(description="Start, as coordinates or a place name.")],
        destination: Annotated[str, Field(description="End, as coordinates or a place name.")],
        via: Annotated[
            str | None,
            Field(
                description=(
                    "Optional intermediate stops, semicolon-separated, visited in the order given. "
                    "Example: '48.87,2.33; 48.88,2.35'"
                )
            ),
        ] = None,
        profile: Annotated[
            Literal["car", "truck", "bicycle", "walk", "motorcycle", "bus", "taxi"],
            Field(
                description=(
                    "Vehicle type. 'truck' respects weight, height, width and hazmat "
                    "restrictions; 'walk' uses footpaths; 'bicycle' prefers cycleways."
                )
            ),
        ] = "car",
        detail: Annotated[
            Literal["summary", "full"],
            Field(
                description=(
                    "'summary' returns distance, duration and turn list. 'full' adds the "
                    "route geometry as GeoJSON, which is much larger."
                )
            ),
        ] = "summary",
        coordinate_format: Annotated[
            Literal["decimal", "grid"],
            Field(
                description=(
                    "'grid' adds a USNG/MGRS grid reference to each turn. Useful for crews "
                    "working from gridded paper maps -- search and rescue, wildland fire, "
                    "surveying."
                )
            ),
        ] = "decimal",
    ) -> RouteResult:
        """Get driving, cycling or walking directions along real roads.

        Returns distance, travel time and turn-by-turn instructions using
        OpenStreetMap road data via Valhalla -- no API key needed.

        Use this for anything involving actual travel: delivery and field-service
        scheduling, ambulance response-time modelling, freight planning with
        vehicle restrictions, commute estimation, hiking and cycling routes.

        For straight-line distance instead, use geom_measure -- it is much cheaper
        and needs no network. To find everywhere reachable within a time budget
        rather than the path to one place, use route_isochrone. For travel times
        between many places at once, use route_matrix rather than calling this
        repeatedly.
        """
        from ..net.providers import valhalla

        start, end = resolve_pair(origin, destination, names=("origin", "destination"))
        stops = [resolve(c.strip(), what="via point") for c in (via or "").split(";") if c.strip()]
        waypoints = [start.as_tuple(), *(stop.as_tuple() for stop in stops), end.as_tuple()]

        payload, cached = valhalla.route(waypoints, profile=profile)
        trip = payload["trip"]
        summary = trip["summary"]
        distance_km = float(summary["length"])
        duration_s = float(summary["time"])

        steps: list[RouteStep] = []
        shape_points: list[tuple[float, float]] = []
        for leg in trip["legs"]:
            decoded = fmt.decode_polyline(leg["shape"], precision=6)
            offset = len(shape_points)
            shape_points.extend(decoded)
            for manoeuvre in leg.get("maneuvers", []):
                index = min(manoeuvre.get("begin_shape_index", 0) + offset, len(shape_points) - 1)
                lat, lon = shape_points[index]
                instruction = manoeuvre.get("instruction", "")
                if coordinate_format == "grid":
                    grid = _safe_grid(lat, lon)
                    if grid:
                        instruction = f"{instruction} [{grid}]"
                steps.append(
                    RouteStep(
                        instruction=instruction,
                        distance_m=round(float(manoeuvre.get("length", 0.0)) * 1000.0, 1),
                        duration_s=round(float(manoeuvre.get("time", 0.0)), 1),
                        latitude=round(lat, 6),
                        longitude=round(lon, 6),
                        street=(manoeuvre.get("street_names") or [None])[0],
                    )
                )

        hours, remainder = divmod(int(duration_s), 3600)
        minutes = remainder // 60
        return RouteResult(
            profile=profile,
            profile_description=valhalla.COSTING_DESCRIPTIONS[profile],
            distance_km=round(distance_km, 3),
            distance_miles=round(distance_km / 1.609344, 3),
            duration_s=round(duration_s, 1),
            duration_display=(f"{hours}h {minutes}m" if hours else f"{minutes}m"),
            start=_endpoint(start, coordinate_format),
            end=_endpoint(end, coordinate_format),
            steps=steps,
            geometry_polyline=fmt.encode_polyline(shape_points, precision=5),
            geometry_geojson=(
                {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in shape_points]}
                if detail == "full"
                else None
            ),
            provider="valhalla",
            meta=meta(
                "valhalla",
                cached=cached,
                resolved=[start, *stops, end],
                notes=(
                    []
                    if detail == "full"
                    else [
                        "Geometry is returned as an encoded polyline (about 10x smaller than "
                        "GeoJSON). Pass detail='full' for GeoJSON coordinates."
                    ]
                ),
            ),
        )

    class IsochroneResult(GeoModel):
        centre: dict[str, float]
        profile: str
        direction: str
        contours: list[dict[str, Any]]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def route_isochrone(
        location: Annotated[
            str, Field(description="Centre point, as coordinates or a place name.")
        ],
        minutes: Annotated[
            str,
            Field(
                description=(
                    "Travel-time contours in minutes, comma-separated. Example: '10,20,30'."
                )
            ),
        ] = "15",
        profile: Annotated[
            Literal["car", "truck", "bicycle", "walk", "motorcycle", "bus", "taxi"],
            Field(description="Vehicle type."),
        ] = "car",
        direction: Annotated[
            Literal["from", "to"],
            Field(
                description=(
                    "'from' = everywhere reachable FROM this point (delivery range). "
                    "'to' = everywhere that can REACH this point (catchment area)."
                )
            ),
        ] = "from",
    ) -> IsochroneResult:
        """Map everywhere reachable within a travel-time budget.

        Returns drive-time (or walk-time, cycle-time) polygons. This is the tool
        for catchment and accessibility questions: which customers are within 30
        minutes of a depot, what a store's 15-minute trade area looks like, how
        many homes a fire station covers in 8 minutes, where to site a warehouse.

        direction='to' computes the reverse catchment, which is the correct choice
        when siting a facility people travel *to*.

        Returns polygons that can be passed straight to geom_relate or geom_overlay
        to count what falls inside them.

        For the path to one specific destination, use route_directions instead.
        """
        from ..net.providers import valhalla

        loc = resolve(location)
        lat, lon = loc.as_tuple()
        try:
            contour_minutes = [float(m.strip()) for m in minutes.split(",") if m.strip()]
        except ValueError:
            raise GeoInputError.with_example(
                got=minutes,
                problem="Contour times must be numbers in minutes.",
                example="10,20,30",
            ) from None
        payload, cached = valhalla.isochrone(
            lat,
            lon,
            profile=profile,
            contours_minutes=contour_minutes,
            reverse=(direction == "to"),
        )
        contours = []
        for feature in payload.get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            props = feature.get("properties") or {}
            measurements = measure_geometry(geometry_from_geojson(geometry))
            contours.append(
                {
                    "minutes": props.get("contour"),
                    "area_km2": round(measurements.get("area_km2", 0.0), 4),
                    "geometry": geometry,
                }
            )
        return IsochroneResult(
            centre={"latitude": round(lat, 6), "longitude": round(lon, 6)},
            profile=profile,
            direction=(
                "reachable from this point" if direction == "from" else "able to reach this point"
            ),
            contours=contours,
            meta=meta("valhalla", cached=cached, resolved=[loc]),
        )

    class MatrixResult(GeoModel):
        profile: str
        origins: list[dict[str, Any]]
        destinations: list[dict[str, Any]]
        duration_s: list[list[float | None]]
        distance_km: list[list[float | None]]
        nearest: list[dict[str, Any] | None]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def route_matrix(
        origins: Annotated[
            str,
            Field(
                description=(
                    "Start points, semicolon-separated, as coordinates or place names. "
                    "Example: '48.8584,2.2945; 48.8606,2.3376'"
                )
            ),
        ],
        destinations: Annotated[
            str | None,
            Field(
                description=(
                    "End points in the same form. Leave empty to measure every origin "
                    "against every other origin."
                )
            ),
        ] = None,
        profile: Annotated[
            Literal["car", "truck", "bicycle", "walk", "motorcycle", "bus", "taxi"],
            Field(description="Vehicle type."),
        ] = "car",
    ) -> MatrixResult:
        """Get road travel times and distances between many origins and destinations at once.

        One call answers "which depot is closest by road to each customer",
        "which crew can reach each job fastest", or ranks candidate sites by
        access. Returns a duration and distance for every origin-destination
        pair, indexed [origin][destination], plus the nearest destination for
        each origin. Unreachable pairs are null.

        For turn-by-turn directions on one trip, use route_directions. For
        everywhere reachable within a time budget, use route_isochrone. For
        straight-line distance, use geom_measure.
        """
        from ..net.providers import valhalla

        square = not (destinations and destinations.strip())
        starts = _parse_stops(origins, "origin")
        ends = starts if square else _parse_stops(destinations or "", "destination")
        payload, cached = valhalla.matrix(
            [loc.as_tuple() for loc in starts], [loc.as_tuple() for loc in ends], profile=profile
        )

        durations: list[list[float | None]] = [[None] * len(ends) for _ in starts]
        distances: list[list[float | None]] = [[None] * len(ends) for _ in starts]
        for row in payload.get("sources_to_targets") or []:
            for cell in row:
                i, j = cell.get("from_index"), cell.get("to_index")
                if i is None or j is None or cell.get("time") is None:
                    continue
                durations[i][j] = round(float(cell["time"]), 1)
                distances[i][j] = round(float(cell["distance"]), 3)

        nearest: list[dict[str, Any] | None] = []
        for i, row in enumerate(durations):
            # In an every-place-to-every-place matrix, the nearest place to an
            # origin is not itself.
            options = [
                (t, j) for j, t in enumerate(row) if t is not None and not (square and i == j)
            ]
            if not options:
                nearest.append(None)
                continue
            best_time, j = min(options)
            nearest.append(
                {
                    "origin": i,
                    "destination": j,
                    "duration_s": best_time,
                    "distance_km": distances[i][j],
                }
            )

        unreachable = sum(
            1
            for i, row in enumerate(durations)
            for j, t in enumerate(row)
            if t is None and not (square and i == j)
        )
        notes = (
            [
                f"{unreachable} origin-destination pair(s) have no route on this network; they are null."
            ]
            if unreachable
            else []
        )
        return MatrixResult(
            profile=profile,
            origins=[_endpoint(loc, "decimal") for loc in starts],
            destinations=[_endpoint(loc, "decimal") for loc in ends],
            duration_s=durations,
            distance_km=distances,
            nearest=nearest,
            meta=meta("valhalla", cached=cached, notes=notes, resolved=[*starts, *ends]),
        )


#: Place names in one matrix call are geocoded one by one against Nominatim's
#: 1 request/second limit, and its usage policy forbids systematic bulk queries.
#: Beyond this, the caller should geocode once and pass coordinates.
_MAX_MATRIX_PLACE_NAMES = 10


def _parse_stops(value: str, what: str) -> list[Location]:
    """Resolve a semicolon-separated list of locations."""
    chunks = [c.strip() for c in value.split(";") if c.strip()]
    if not chunks:
        raise GeoInputError.with_example(
            got=value,
            problem=f"No {what}s were given.",
            example="48.8584,2.2945; 48.8606,2.3376",
        )
    names = [c for c in chunks if not parse_location(c).resolved]
    if len(names) > _MAX_MATRIX_PLACE_NAMES:
        raise GeoInputError.with_example(
            got=f"{len(names)} place names",
            problem=(
                f"At most {_MAX_MATRIX_PLACE_NAMES} {what}s per call may be place names, "
                "because each is geocoded separately against a rate-limited public "
                "service. Geocode them once with place_geocode and pass coordinates."
            ),
            example="48.8584,2.2945; 48.8606,2.3376; 48.8530,2.3499",
        )
    return [resolve(c, what=what) for c in chunks]


def _endpoint(loc, coordinate_format: str) -> dict[str, Any]:
    lat, lon = loc.as_tuple()
    out: dict[str, Any] = {
        "input": loc.raw,
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
    }
    if coordinate_format == "grid":
        out["grid_reference"] = _safe_grid(lat, lon)
    return out


# -------------------------------------------------------------------- terrain
def _register_terrain(mcp: MCPServer) -> None:
    class ElevationResult(GeoModel):
        latitude: float
        longitude: float
        elevation_m: float | None
        elevation_ft: float | None
        vertical_datum: str
        dataset: str
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def terrain_elevation(
        location: Annotated[str, Field(description="Position, as coordinates or a place name.")],
    ) -> ElevationResult:
        """Get ground elevation above sea level at a location.

        Returns height in metres and feet from the Copernicus GLO-90 global DEM
        (90 m resolution), relative to the EGM2008 geoid -- that is, orthometric
        height above mean sea level, which is what "elevation" normally means.
        GNSS receivers report ellipsoidal height, which differs by tens of metres.

        For elevation along a path rather than at one point, use terrain_profile --
        it fetches up to 100 samples in a single request.
        """
        from ..net.providers import openmeteo

        loc = resolve(location)
        lat, lon = loc.as_tuple()
        values, cached = openmeteo.elevations([(lat, lon)])
        elevation = values[0] if values else None
        notes = (
            []
            if elevation is not None
            else [
                "No elevation data at this position; the DEM has a gap here "
                "(usually open water or a void in the source data)."
            ]
        )
        return ElevationResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            elevation_m=elevation,
            elevation_ft=round(elevation * 3.28084, 2) if elevation is not None else None,
            vertical_datum="EGM2008 geoid (height above mean sea level)",
            dataset="Copernicus GLO-90",
            meta=meta("open-meteo-elevation", cached=cached, notes=notes, resolved=[loc]),
        )

    class ProfileSample(GeoModel):
        distance_m: float
        latitude: float
        longitude: float
        elevation_m: float | None

    class ProfileResult(GeoModel):
        total_distance_m: float
        samples: int
        statistics: dict[str, Any]
        profile: list[ProfileSample]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def terrain_profile(
        origin: Annotated[str, Field(description="Start of the path.")],
        destination: Annotated[str, Field(description="End of the path.")],
        samples: Annotated[
            int,
            Field(
                ge=2,
                le=100,
                description=("Number of evenly-spaced elevation samples along the path."),
            ),
        ] = 50,
    ) -> ProfileResult:
        """Get the terrain elevation profile along a path between two points.

        Samples the ground along a true geodesic and returns the full profile plus
        statistics: minimum, maximum, relief, total ascent and descent. All samples
        come from one batched request rather than one call per point.

        Use this for cycling and hiking route difficulty, pipeline and cable
        routing, drainage assessment, or sizing earthworks. For the elevation at a
        single point, or at a scattered set of them, use terrain_elevation -- this
        tool always samples the straight line between two ends.
        """
        from ..net.providers import openmeteo

        start, end = resolve_pair(origin, destination, names=("origin", "destination"))
        lat1, lon1 = start.as_tuple()
        lat2, lon2 = end.as_tuple()
        points = geodesy.densify(lat1, lon1, lat2, lon2, clamp(samples, 2, 100))
        elevations, cached = openmeteo.elevations([(lat, lon) for lat, lon, _ in points])
        stats = terrain_statistics(elevations)
        notes = []
        if stats.get("gaps"):
            notes.append(
                f"{stats['gaps']} of {len(points)} samples had no elevation data and are "
                "reported as null rather than substituted with zero."
            )
        return ProfileResult(
            total_distance_m=round(points[-1][2], 2),
            samples=len(points),
            statistics=stats,
            profile=[
                ProfileSample(
                    distance_m=round(distance, 1),
                    latitude=round(lat, 6),
                    longitude=round(lon, 6),
                    elevation_m=elevation,
                )
                for (lat, lon, distance), elevation in zip(points, elevations, strict=True)
            ],
            meta=meta("open-meteo-elevation", cached=cached, notes=notes, resolved=[start, end]),
        )


# ---------------------------------------------------------------- environment
def _register_environment(mcp: MCPServer) -> None:
    class WeatherResult(GeoModel):
        latitude: float
        longitude: float
        timezone: str | None = None
        current: dict[str, Any] = {}
        daily: list[dict[str, Any]] = []
        hourly_summary: dict[str, Any] = {}
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def env_weather(
        location: Annotated[str, Field(description="Position, as coordinates or a place name.")],
        days: Annotated[int, Field(ge=1, le=16, description="Forecast days to return.")] = 3,
        include_hourly: Annotated[
            bool, Field(description=("Include an hourly breakdown for the first 24 hours."))
        ] = False,
    ) -> WeatherResult:
        """Get current weather and a multi-day forecast for a location.

        Returns temperature, precipitation, wind, cloud cover, humidity and
        visibility from Open-Meteo -- no API key needed.

        Useful for delivery and field-work scheduling, construction and outdoor
        event planning, agricultural spray windows, and travel decisions.

        Note the free Open-Meteo tier is licensed for non-commercial use; set
        OPEN_METEO_API_KEY and OPEN_METEO_BASE_URL for commercial deployments.
        """
        from ..net.providers import openmeteo

        loc = resolve(location)
        lat, lon = loc.as_tuple()
        payload, cached = openmeteo.forecast(lat, lon, forecast_days=days)
        current = dict(payload.get("current") or {})
        if "weather_code" in current:
            current["conditions"] = openmeteo.WMO_CODES.get(int(current["weather_code"]), "Unknown")
        daily_raw = payload.get("daily") or {}
        daily = []
        for index, day in enumerate(daily_raw.get("time", [])):
            entry: dict[str, Any] = {"date": day}
            for key, values in daily_raw.items():
                if key == "time" or index >= len(values):
                    continue
                entry[key] = values[index]
            if "weather_code" in entry and entry["weather_code"] is not None:
                entry["conditions"] = openmeteo.WMO_CODES.get(int(entry["weather_code"]), "Unknown")
            daily.append(entry)

        hourly_summary: dict[str, Any] = {}
        if include_hourly:
            hourly_raw = payload.get("hourly") or {}
            hourly_summary = {
                key: values[:24] for key, values in hourly_raw.items() if isinstance(values, list)
            }
        return WeatherResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            timezone=payload.get("timezone"),
            current=current,
            daily=daily,
            hourly_summary=hourly_summary,
            meta=meta(
                "open-meteo",
                cached=cached,
                notes=["Open-Meteo's free tier is licensed for non-commercial use."],
                resolved=[loc],
            ),
        )

    class SunMoonResult(GeoModel):
        latitude: float
        longitude: float
        date: str
        timezone: str | None
        sun: dict[str, Any]
        moon: dict[str, Any]
        position_now: dict[str, float] | None = None
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def sun_moon(
        location: Annotated[str, Field(description="Position, as coordinates or a place name.")],
        on_date: Annotated[
            str | None, Field(description=("Date as YYYY-MM-DD. Defaults to today (UTC)."))
        ] = None,
    ) -> SunMoonResult:
        """Get sunrise, sunset, twilight, golden hour and moon phase for a location.

        Computed offline from astronomical formulas -- no network needed.

        Includes the shadow-length ratio, which is what construction shadow
        studies, right-to-light assessments and solar-panel shading analysis
        actually need: a 10 m object at 30 degrees solar elevation casts a 17.3 m
        shadow. Also used for photography scheduling, crop modelling, delivery
        windows before dusk, and retail daylight-hours analysis.

        Inside the polar circles, events that do not occur are returned as null
        with an explanation rather than as an error.
        """
        loc = resolve(location)
        lat, lon = loc.as_tuple()
        try:
            when = date.fromisoformat(on_date) if on_date else datetime.now(UTC).date()
        except ValueError:
            raise GeoInputError.with_example(
                got=on_date,
                problem="Date must be in ISO format.",
                example="2026-06-21",
            ) from None
        tz = timezones.timezone_at(lat, lon) or "UTC"
        sun_data = solar.sun_times(lat, lon, when, tz)
        position = (
            solar.sun_position(lat, lon, datetime.now(UTC))
            if when == datetime.now(UTC).date()
            else None
        )
        return SunMoonResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            date=when.isoformat(),
            timezone=tz,
            sun=sun_data,
            moon=solar.moon_info(when),
            position_now=position,
            meta=meta(offline=True, notes=sun_data.pop("notes", []), resolved=[loc]),
        )

    class TimeResult(GeoModel):
        latitude: float
        longitude: float
        info: dict[str, Any]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def time_at_location(
        location: Annotated[str, Field(description="Position, as coordinates or a place name.")],
    ) -> TimeResult:
        """Get the time zone and current local time at a location.

        Returns the IANA zone name, current local time, UTC offset, whether
        daylight saving is active, and when the next DST transition happens.
        Computed offline from a bundled boundary dataset.

        Useful for scheduling across sites, interpreting timestamps in GPS traces,
        and working out delivery or support-window overlaps.
        """
        loc = resolve(location)
        lat, lon = loc.as_tuple()
        return TimeResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            info=timezones.local_time_info(lat, lon),
            meta=meta(offline=True, resolved=[loc]),
        )


# ----------------------------------------------------------------------- data
def _register_data(mcp: MCPServer) -> None:
    class ConvertResult(GeoModel):
        input_format: str
        output_format: str
        output: str
        measurements: dict[str, Any]
        validation: dict[str, Any] | None = None
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def data_convert(
        data: Annotated[
            GeometryInput,
            Field(
                description=(
                    "The geometry or dataset to convert: GeoJSON (object or string), "
                    "WKT, WKB hex, an encoded polyline, KML, GPX, or CSV with "
                    "latitude/longitude columns."
                )
            ),
        ],
        to_format: Annotated[
            Literal["geojson", "wkt", "wkb", "polyline", "kml", "gpx"],
            Field(description="Output format."),
        ] = "geojson",
        from_format: Annotated[
            Literal["auto", "geojson", "wkt", "wkb", "polyline", "kml", "gpx", "csv"],
            Field(description="Input format; 'auto' detects it."),
        ] = "auto",
    ) -> ConvertResult:
        """Convert geospatial data between GeoJSON, WKT, WKB, KML, GPX, CSV and polyline.

        Detects the input format automatically and validates the result, reporting
        specific problems (self-intersections, out-of-range coordinates, the
        removed RFC 7946 'crs' member) rather than a bare pass/fail.

        Handles the everyday format wrangling of real work: a customer sends KML,
        the routing engine wants an encoded polyline, the warehouse system exports
        CSV, the web map needs GeoJSON. Runs entirely offline.
        """
        data = as_geometry_text(data) or ""
        detected: str = from_format
        if from_format == "csv" or (from_format == "auto" and _looks_like_csv(data)):
            detected = "csv"
            rows = fmt.points_from_csv(data)
            from shapely.geometry import MultiPoint

            geom = MultiPoint([(r["longitude"], r["latitude"]) for r in rows])
        else:
            geom = fmt.from_any(data, from_format)
            if from_format == "auto":
                detected = fmt._sniff(data.strip())

        if to_format == "geojson":
            import json as _json

            output = _json.dumps(fmt.to_geojson(geom))
        elif to_format == "wkt":
            output = fmt.to_wkt_text(geom)
        elif to_format == "wkb":
            output = fmt.to_wkb_hex(geom)
        elif to_format == "polyline":
            coords = (
                list(geom.coords)
                if geom.geom_type == "LineString"
                else [(p.x, p.y) for p in getattr(geom, "geoms", [geom])]
            )
            output = fmt.encode_polyline([(y, x) for x, y in coords])
        elif to_format == "kml":
            output = fmt.to_kml(geom)
        else:  # gpx
            coords = (
                list(geom.coords)
                if geom.geom_type == "LineString"
                else [(p.x, p.y) for p in getattr(geom, "geoms", [geom])]
            )
            output = fmt.to_gpx([(y, x) for x, y in coords])

        validation = fmt.validate_geojson(output) if to_format == "geojson" else None
        return ConvertResult(
            input_format=detected,
            output_format=to_format,
            output=output,
            measurements=measure_geometry(geom),
            validation=validation,
            meta=meta(offline=True),
        )


def _looks_like_csv(text: str) -> bool:
    head = text.strip().splitlines()[:1]
    if not head:
        return False
    first = head[0].lower()
    return "," in first and any(
        token in first for token in ("lat", "lon", "lng", "latitude", "longitude")
    )


# --------------------------------------------------------------- capabilities
def _register_capabilities(mcp: MCPServer) -> None:
    class ToolsetInfo(GeoModel):
        name: str
        summary: str
        enabled: bool
        offline: bool
        requires_extra: str | None = None

    class CapabilitiesResult(GeoModel):
        version: str
        enabled_toolsets: list[str]
        toolsets: list[ToolsetInfo]
        api_keys: dict[str, Any]
        cache: dict[str, Any]
        how_to_enable: str
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def geo_capabilities() -> CapabilitiesResult:
        """List which geospatial toolsets are enabled and what else is available.

        Call this when a capability seems to be missing, before telling the user
        something is impossible -- the tool they need may simply be in a toolset
        that is switched off, and this returns the exact command to enable it.

        Also reports which optional API keys are configured and where the response
        cache lives.
        """
        from .. import __version__
        from ..config import settings
        from . import TOOLSETS

        enabled = set(_ENABLED_TOOLSETS)
        return CapabilitiesResult(
            version=__version__,
            enabled_toolsets=sorted(enabled),
            toolsets=[
                ToolsetInfo(
                    name=ts.name,
                    summary=ts.summary,
                    enabled=(name in enabled),
                    offline=ts.offline,
                    requires_extra=ts.extra,
                )
                for name, ts in sorted(TOOLSETS.items())
            ],
            api_keys={
                "openrouteservice": bool(settings.openrouteservice_key),
                "opencellid": bool(settings.opencellid_key),
                "open_meteo": bool(settings.open_meteo_key),
                "note": (
                    "No API key is required for normal use. Every keyed provider has a "
                    "keyless default: routing uses Valhalla, geocoding uses Nominatim "
                    "and Photon, cell towers use BeaconDB."
                ),
            },
            cache={"enabled": settings.cache_enabled, "directory": str(settings.cache_dir)},
            how_to_enable=(
                "Set GEO_TOOLSETS to a comma-separated list, or pass --toolsets. "
                "Aliases: 'default' (core+rf), 'all', and 'offline'. The 'offline' "
                "alias keeps only tools that never touch the network under any input "
                "-- which excludes geom_measure and place lookups, since those may "
                "geocode a place name."
            ),
            meta=meta(offline=True),
        )


#: Populated by ``build_server`` so ``geo_capabilities`` can report accurately.
_ENABLED_TOOLSETS: list[str] = []


def set_enabled_toolsets(names: list[str]) -> None:
    _ENABLED_TOOLSETS[:] = names
