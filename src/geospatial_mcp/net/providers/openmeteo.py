"""Open-Meteo: weather, elevation and air quality. Keyless.

Licence note that belongs in front of users, not in a footnote: the free tier
is **CC-BY 4.0 and non-commercial**. Commercial deployments must either point
``OPEN_METEO_BASE_URL`` at ``customer-api.open-meteo.com`` with an API key, or
self-host.

Elevation comes from the Copernicus GLO-90 DEM and accepts up to 100
coordinates in a single request, which is what makes terrain profiles and
line-of-sight checks one call instead of fifty.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...config import settings
from ...errors import GeoUpstreamError
from ..client import get_client

__all__ = ["WMO_CODES", "air_quality", "archive", "elevations", "forecast"]

#: WMO 4677 present-weather codes, as used by Open-Meteo.
WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

#: Open-Meteo's documented maximum coordinates per elevation request.
MAX_ELEVATION_POINTS = 100

#: How long an elevation response stays cached. The host-wide lifetime for
#: api.open-meteo.com is ten minutes, because the same host serves forecasts.
#: Elevation comes from a static DEM, so inheriting that lifetime only meant a
#: terrain profile was fetched again every ten minutes and could never be
#: warmed ahead of time. A year matches the other DEM sources in the cache.
ELEVATION_TTL_S = 365 * 86400.0


def _auth_params() -> dict[str, Any]:
    return {"apikey": settings.open_meteo_key} if settings.open_meteo_key else {}


def elevations(points: Sequence[tuple[float, float]]) -> tuple[list[float | None], bool]:
    """Batch elevation lookup. Returns metres above the EGM2008 geoid.

    ``None`` is preserved for gaps rather than substituted with 0.0. Coercing a
    missing value to sea level silently turns a data void into "clear line of
    sight over flat ground" -- a wrong answer that looks entirely plausible, and
    one nothing downstream can detect.
    """
    if not points:
        return [], False
    if len(points) > MAX_ELEVATION_POINTS:
        raise GeoUpstreamError(
            f"Open-Meteo accepts at most {MAX_ELEVATION_POINTS} coordinates per "
            f"elevation request; got {len(points)}. Reduce the sample count."
        )
    # A 100-point batch is a much heavier request than a single lookup, and the
    # public endpoint is regularly slow under load. Scale the timeout with the
    # batch size rather than failing a terrain profile on a 15s default.
    timeout_s = 20.0 + 0.2 * len(points)
    payload, cached = get_client().get_json(
        f"{settings.open_meteo_url}/v1/elevation",
        params={
            "latitude": ",".join(f"{lat:.6f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.6f}" for _, lon in points),
            **_auth_params(),
        },
        provider="Open-Meteo",
        ttl_s=ELEVATION_TTL_S,
        timeout_s=timeout_s,
    )
    values = payload.get("elevation") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise GeoUpstreamError("Open-Meteo elevation response had no 'elevation' array.")
    return [None if v is None else float(v) for v in values], cached


def forecast(
    lat: float,
    lon: float,
    *,
    forecast_days: int = 3,
    past_days: int = 0,
    hourly: Sequence[str] | None = None,
    daily: Sequence[str] | None = None,
    timezone: str = "auto",
) -> tuple[dict[str, Any], bool]:
    """Current conditions plus hourly and daily forecast."""
    params: dict[str, Any] = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "current": ",".join(
            (
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "precipitation",
                "weather_code",
                "cloud_cover",
                "surface_pressure",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
                "is_day",
            )
        ),
        "hourly": ",".join(
            hourly
            or (
                "temperature_2m",
                "precipitation_probability",
                "precipitation",
                "wind_speed_10m",
                "visibility",
            )
        ),
        "daily": ",".join(
            daily
            or (
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "sunrise",
                "sunset",
            )
        ),
        "timezone": timezone,
        "forecast_days": max(1, min(forecast_days, 16)),
        **_auth_params(),
    }
    if past_days:
        params["past_days"] = max(0, min(past_days, 92))
    payload, cached = get_client().get_json(
        f"{settings.open_meteo_url}/v1/forecast", params=params, provider="Open-Meteo"
    )
    if not isinstance(payload, dict):
        raise GeoUpstreamError("Open-Meteo forecast returned an unexpected payload.")
    return payload, cached


def archive(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    *,
    daily: Sequence[str] | None = None,
    timezone: str = "auto",
) -> tuple[dict[str, Any], bool]:
    """Historical reanalysis, 1940 to about five days ago. Dates are YYYY-MM-DD."""
    payload, cached = get_client().get_json(
        f"{settings.open_meteo_archive_url}/v1/archive",
        params={
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "start_date": start_date,
            "end_date": end_date,
            "daily": ",".join(
                daily
                or (
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "wind_speed_10m_max",
                )
            ),
            "timezone": timezone,
            **_auth_params(),
        },
        provider="Open-Meteo archive",
    )
    if not isinstance(payload, dict):
        raise GeoUpstreamError("Open-Meteo archive returned an unexpected payload.")
    return payload, cached


def air_quality(lat: float, lon: float, *, timezone: str = "auto") -> tuple[dict[str, Any], bool]:
    """Current air quality: PM2.5, PM10, ozone, NO2, SO2 and the European AQI."""
    payload, cached = get_client().get_json(
        f"{settings.open_meteo_air_quality_url}/v1/air-quality",
        params={
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "current": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphour_dioxide,ozone,european_aqi,us_aqi",
            "timezone": timezone,
            **_auth_params(),
        },
        provider="Open-Meteo air quality",
    )
    if not isinstance(payload, dict):
        raise GeoUpstreamError("Open-Meteo air-quality returned an unexpected payload.")
    return payload, cached
