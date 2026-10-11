"""The analysis toolset: clustering, density and spatial indexing.

Gated behind the ``stats`` extra, which is also what exercises the toolset
dependency-gating path -- ``core`` and ``rf`` require nothing optional.
"""

# NOTE: no `from __future__ import annotations` -- see tools/core.py.

from typing import Annotated, Literal

from pydantic import Field

from .._sdk import MCPServer, compute
from ..errors import GeoInputError
from ..geo import grids
from ..models import GeoModel, ResponseMeta
from ._shared import meta, require_metres, resolve

__all__ = ["register"]


def register(mcp: MCPServer) -> None:
    class ClusterInfo(GeoModel):
        label: int
        size: int
        centroid: dict[str, float]
        radius_m: float
        member_indices: list[int]

    class ClusterResult(GeoModel):
        algorithm: str
        point_count: int
        cluster_count: int
        clusters: list[ClusterInfo]
        noise_indices: list[int]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def stats_cluster(
        points: Annotated[
            str,
            Field(
                description=(
                    "Points to cluster, semicolon-separated, each 'lat,lon'. "
                    "Example: '48.86,2.29; 48.87,2.30; 51.50,-0.12'"
                )
            ),
        ],
        eps_m: Annotated[
            float,
            Field(gt=0, description="Neighbourhood radius in metres (DBSCAN only)."),
        ] = 500.0,
        min_samples: Annotated[
            int, Field(ge=2, description="Minimum points to form a cluster.")
        ] = 3,
        algorithm: Annotated[
            Literal["dbscan", "hdbscan"],
            Field(
                description=(
                    "dbscan: one fixed neighbourhood size. hdbscan: adapts per region, "
                    "so it copes with dense urban and sparse rural clusters together."
                )
            ),
        ] = "dbscan",
    ) -> ClusterResult:
        """Group geographic points into clusters by ground distance.

        Finds where points concentrate: delivery failures, insurance claims,
        crime incidents, equipment faults, customer addresses, wildlife
        sightings. Points belonging to no cluster are returned as noise rather
        than forced into one, which keeps isolated outliers visible.

        Distances are true ground metres via the haversine metric. Clustering on
        raw latitude/longitude instead stretches clusters east-west by
        1/cos(latitude) -- at 55 degrees north that is a factor of nearly two.

        For counting points into fixed cells rather than finding natural
        groupings, use coord_grid_cells.
        """
        from ..geo.stats import cluster_points

        if algorithm == "dbscan":  # hdbscan ignores eps_m, so it cannot mislead
            require_metres(eps_m, name="eps_m", example="500  (points within 500 m are neighbours)")
        parsed = _parse_points(points)
        clusters, noise = cluster_points(
            parsed, eps_m=eps_m, min_samples=min_samples, algorithm=algorithm
        )
        return ClusterResult(
            algorithm=algorithm,
            point_count=len(parsed),
            cluster_count=len(clusters),
            clusters=[
                ClusterInfo(
                    label=c.label,
                    size=c.size,
                    centroid={
                        "latitude": round(c.centroid_latitude, 6),
                        "longitude": round(c.centroid_longitude, 6),
                    },
                    radius_m=round(c.radius_m, 1),
                    member_indices=c.member_indices,
                )
                for c in clusters
            ],
            noise_indices=noise,
            meta=meta(
                offline=True,
                notes=(
                    [f"{len(noise)} point(s) belong to no cluster and are listed as noise."]
                    if noise
                    else []
                ),
            ),
        )

    class BinCount(GeoModel):
        cell: str
        count: int
        centre: dict[str, float]

    class GridResult(GeoModel):
        system: str
        resolution: int
        cell_count: int
        point_count: int
        cells: list[BinCount]
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def coord_grid_cells(
        points: Annotated[
            str,
            Field(
                description=(
                    "Points to bin, semicolon-separated, each 'lat,lon'. "
                    "Example: '48.86,2.29; 48.87,2.30'"
                )
            ),
        ],
        system: Annotated[
            Literal["h3", "geohash"],
            Field(
                description=(
                    "h3: hexagons, uniform adjacency, the usual choice for demand "
                    "modelling. geohash: rectangles, prefix-shareable, the usual "
                    "choice for key-value stores."
                )
            ),
        ] = "h3",
        resolution: Annotated[
            int,
            Field(
                ge=0,
                le=15,
                description=(
                    "H3 resolution 0-15 (9 is about 0.1 km2), or geohash length 1-12 "
                    "(7 is about 150 m)."
                ),
            ),
        ] = 8,
    ) -> GridResult:
        """Bin points into H3 hexagons or geohash cells and count them.

        Aggregates scattered points into fixed cells for heat-mapping, demand
        modelling, delivery-zone sizing, privacy-preserving location reporting
        and joining datasets that share no common key but share geography.

        For finding natural groupings rather than counting into a fixed grid,
        use stats_cluster.
        """
        parsed = _parse_points(points)
        counts: dict[str, int] = {}
        for lat, lon in parsed:
            if system == "h3":
                cell = grids.h3_encode(lat, lon, resolution)
            else:
                cell = grids.geohash_encode(lat, lon, max(1, min(resolution, 12)))
            counts[cell] = counts.get(cell, 0) + 1

        cells = []
        for cell, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            centre = grids.h3_decode(cell) if system == "h3" else grids.geohash_decode(cell)
            cells.append(
                BinCount(
                    cell=cell,
                    count=count,
                    centre={"latitude": round(centre[0], 6), "longitude": round(centre[1], 6)},
                )
            )
        return GridResult(
            system=system,
            resolution=resolution,
            cell_count=len(cells),
            point_count=len(parsed),
            cells=cells,
            meta=meta(offline=True),
        )


def _parse_points(value: str) -> list[tuple[float, float]]:
    """Parse a semicolon-separated list of positions."""
    parsed: list[tuple[float, float]] = []
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if chunk:
            parsed.append(resolve(chunk, what="point", allow_geocode=False).as_tuple())
    if len(parsed) < 2:
        raise GeoInputError.with_example(
            got=value,
            problem="At least two points are needed.",
            example="48.86,2.29; 48.87,2.30; 48.88,2.31",
        )
    return parsed
