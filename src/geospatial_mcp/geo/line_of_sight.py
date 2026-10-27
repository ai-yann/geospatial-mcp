"""Sampled radio-path clearance, without a diffraction-loss prediction.

References, checked against the published recommendations:
  ITU-R P.526-15, Annex 1, section 2.1, equation (2): first Fresnel radius.
  ITU-R P.526-15, Annex 1, section 4.4, equation (44): effective Earth radius
  k * 6371 km. The small-angle bulge d1*d2/(2*k*R) gives the conventional
  d1*d2/(12.74*k) metres when distances are in kilometres.
  ITU-R P.530-18, Annex 1, equation (3) and section 2.2.2: practical Fresnel
  units and the 60% clearance rule. This is a screening rule, not the complete
  availability-design procedure in that recommendation.
  https://www.itu.int/rec/R-REC-P.526/en
  https://www.itu.int/rec/R-REC-P.530/en
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

from ..errors import GeoInputError

__all__ = [
    "ClearancePoint",
    "LineOfSight",
    "earth_curvature_bulge_m",
    "first_fresnel_radius_m",
    "path_clearance",
]


def _require_distances(d1_km: float, d2_km: float) -> None:
    if not all(math.isfinite(d) and d >= 0 for d in (d1_km, d2_km)):
        raise GeoInputError.with_example(
            got=(d1_km, d2_km),
            problem="Distances from the path terminals must be finite and non-negative.",
            example="2.5 and 2.5 km",
        )


def earth_curvature_bulge_m(d1_km: float, d2_km: float, k_factor: float = 4 / 3) -> float:
    """Effective-Earth bulge in metres, from ITU-R P.526 equation (44).

    Uses the conventional rounded Earth diameter 12,740 km. Positive bulge
    raises the surface relative to the chord between the antenna tops.
    """
    _require_distances(d1_km, d2_km)
    if not math.isfinite(k_factor) or k_factor <= 0:
        raise GeoInputError.with_example(
            got=k_factor,
            problem="k_factor must be finite and positive.",
            example="1.3333333333333333  (standard 4/3 effective Earth radius)",
        )
    return d1_km * d2_km / (12.74 * k_factor)


def first_fresnel_radius_m(d1_km: float, d2_km: float, frequency_mhz: float) -> float:
    """ITU-R P.526 equation (2), in the practical units of P.530 equation (3).

    17.32 * sqrt(d1*d2 / (f_GHz*(d1+d2))). The coefficient rounds the
    wavelength-based value to two decimals. The radius is zero at a terminal.
    """
    _require_distances(d1_km, d2_km)
    if not math.isfinite(frequency_mhz) or frequency_mhz <= 0:
        raise GeoInputError.with_example(
            got=frequency_mhz,
            problem="frequency_mhz must be finite and positive.",
            example="5800  (a 5.8 GHz link)",
        )
    if d1_km + d2_km == 0:
        raise GeoInputError.with_example(
            got=(d1_km, d2_km),
            problem="The path must have positive total distance.",
            example="2.5 and 2.5 km",
        )
    return 17.32 * math.sqrt(d1_km * d2_km / ((frequency_mhz / 1000) * (d1_km + d2_km)))


@dataclass(frozen=True)
class ClearancePoint:
    distance_m: float
    ground_elevation_m: float
    sightline_height_m: float
    earth_bulge_m: float
    clearance_m: float
    first_fresnel_radius_m: float | None = None
    fresnel_clearance_fraction: float | None = None


@dataclass(frozen=True)
class LineOfSight:
    clear: bool | None
    worst_point: ClearancePoint | None
    sightline_height_m: list[float | None]
    clearance_m: list[float | None]
    fresnel_60_percent_clear: bool | None = None
    worst_fresnel_point: ClearancePoint | None = None


def path_clearance(
    distances_m: list[float],
    elevations_m: list[float | None],
    *,
    origin_antenna_height_m: float,
    destination_antenna_height_m: float,
    frequency_mhz: float | None = None,
    k_factor: float = 4 / 3,
) -> LineOfSight:
    """Compare surface samples with the effective sightline between mast tops.

    A known obstruction establishes blockage even if another sample is missing.
    Missing data can never establish a clear path. Fresnel clearance is tested
    along the whole path, since its limiting point can differ from the point
    with the least geometric clearance. Endpoint Fresnel radii are zero.
    """
    if (
        len(distances_m) < 2
        or len(distances_m) != len(elevations_m)
        or distances_m[0] != 0
        or any(not math.isfinite(d) for d in distances_m)
        or any(b <= a for a, b in pairwise(distances_m))
    ):
        raise GeoInputError.with_example(
            got=distances_m,
            problem="Profile distances must start at zero and increase, one per elevation.",
            example="distances_m=[0, 2500, 5000], elevations_m=[100, 110, 100]",
        )
    if not all(
        math.isfinite(h) and h >= 0 for h in (origin_antenna_height_m, destination_antenna_height_m)
    ):
        raise GeoInputError.with_example(
            got=(origin_antenna_height_m, destination_antenna_height_m),
            problem="Antenna heights above the surface must be finite and non-negative.",
            example="origin_antenna_height_m=10, destination_antenna_height_m=10",
        )
    total_m = distances_m[-1]
    # Validate assumptions even when a DEM gap prevents evaluating the path.
    earth_curvature_bulge_m(0, total_m / 1000, k_factor)
    if frequency_mhz is not None:
        first_fresnel_radius_m(0, total_m / 1000, frequency_mhz)
    if any(e is not None and not math.isfinite(e) for e in elevations_m):
        raise GeoInputError.with_example(
            got=elevations_m,
            problem="Elevations must be finite metres or None for missing samples.",
            example="[100, None, 110]",
        )
    ground_start, ground_end = elevations_m[0], elevations_m[-1]
    if ground_start is None or ground_end is None:
        missing: list[float | None] = [None] * len(distances_m)
        return LineOfSight(None, None, missing, missing.copy())
    start_m = ground_start + origin_antenna_height_m
    end_m = ground_end + destination_antenna_height_m
    points: list[ClearancePoint] = []
    sightline: list[float | None] = []
    clearance: list[float | None] = []
    for distance, elevation in zip(distances_m, elevations_m, strict=True):
        d1, d2 = distance / 1000, (total_m - distance) / 1000
        bulge = earth_curvature_bulge_m(d1, d2, k_factor)
        height = start_m + (end_m - start_m) * distance / total_m - bulge
        sightline.append(height)
        if elevation is None:
            clearance.append(None)
            continue
        gap = height - elevation
        radius = (
            first_fresnel_radius_m(d1, d2, frequency_mhz) if frequency_mhz is not None else None
        )
        points.append(
            ClearancePoint(
                distance,
                elevation,
                height,
                bulge,
                gap,
                radius,
                gap / radius if radius is not None and radius > 0 else None,
            )
        )
        clearance.append(gap)
    worst = min(points, key=lambda p: p.clearance_m)
    complete = all(e is not None for e in elevations_m)
    clear = False if worst.clearance_m <= 0 else (True if complete else None)
    fresnel_points = [p for p in points if p.fresnel_clearance_fraction is not None]
    worst_fresnel = min(
        fresnel_points,
        key=lambda p: (
            p.fresnel_clearance_fraction if p.fresnel_clearance_fraction is not None else math.inf
        ),
        default=None,
    )
    fresnel_clear = None
    if worst_fresnel is not None:
        fraction = worst_fresnel.fresnel_clearance_fraction
        assert fraction is not None
        fresnel_clear = False if fraction < 0.6 else (True if complete else None)
    return LineOfSight(clear, worst, sightline, clearance, fresnel_clear, worst_fresnel)
