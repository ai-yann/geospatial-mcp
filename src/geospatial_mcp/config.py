"""Runtime configuration, read from the environment.

Deliberately stdlib-only: no pydantic-settings, no python-dotenv. An MCP server
launched over stdio inherits its environment from the client's config file, so
a ``.env`` loader would be dead weight in the common case and a surprise in the
uncommon one.

Every provider base URL is overridable so that anyone hitting the public
instances' rate limits can point at a self-hosted Nominatim, Photon, Overpass,
Valhalla or Open-Meteo without patching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

__all__ = ["Settings", "default_cache_dir", "settings"]

#: Public instances used when nothing is configured. All keyless.
_DEFAULTS = {
    "NOMINATIM_URL": "https://nominatim.openstreetmap.org",
    "PHOTON_URL": "https://photon.komoot.io",
    "OVERPASS_URL": "https://overpass-api.de/api/interpreter",
    "VALHALLA_URL": "https://valhalla1.openstreetmap.de",
    "OPEN_METEO_BASE_URL": "https://api.open-meteo.com",
    "OPEN_METEO_ARCHIVE_URL": "https://archive-api.open-meteo.com",
    "OPEN_METEO_AIR_QUALITY_URL": "https://air-quality-api.open-meteo.com",
    "OPENTOPODATA_URL": "https://api.opentopodata.org",
    "DEM_TILE_URL": "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png",
    "BEACONDB_URL": "https://api.beacondb.net",
    "USGS_EARTHQUAKE_URL": "https://earthquake.usgs.gov",
    "NWS_URL": "https://api.weather.gov",
    "CELESTRAK_URL": "https://celestrak.org",
}


def default_cache_dir() -> Path:
    """Return the platform cache directory without taking a platformdirs dep."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "mcp-geospatial" / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "mcp-geospatial"
    return Path.home() / ".cache" / "mcp-geospatial"


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the process environment."""

    # --- provider endpoints -------------------------------------------------
    nominatim_url: str = _DEFAULTS["NOMINATIM_URL"]
    photon_url: str = _DEFAULTS["PHOTON_URL"]
    overpass_url: str = _DEFAULTS["OVERPASS_URL"]
    valhalla_url: str = _DEFAULTS["VALHALLA_URL"]
    open_meteo_url: str = _DEFAULTS["OPEN_METEO_BASE_URL"]
    open_meteo_archive_url: str = _DEFAULTS["OPEN_METEO_ARCHIVE_URL"]
    open_meteo_air_quality_url: str = _DEFAULTS["OPEN_METEO_AIR_QUALITY_URL"]
    opentopodata_url: str = _DEFAULTS["OPENTOPODATA_URL"]
    dem_tile_url: str = _DEFAULTS["DEM_TILE_URL"]
    beacondb_url: str = _DEFAULTS["BEACONDB_URL"]
    usgs_earthquake_url: str = _DEFAULTS["USGS_EARTHQUAKE_URL"]
    nws_url: str = _DEFAULTS["NWS_URL"]
    celestrak_url: str = _DEFAULTS["CELESTRAK_URL"]

    # --- optional API keys (every one has a keyless default path) -----------
    openrouteservice_key: str | None = None
    opencellid_key: str | None = None
    open_meteo_key: str | None = None
    firms_map_key: str | None = None

    # --- behaviour ----------------------------------------------------------
    user_agent: str = ""
    cache_dir: Path = field(default_factory=default_cache_dir)
    cache_enabled: bool = True
    toolsets: str = "default"
    trace_file: Path | None = None
    trace_results: bool = False
    http_timeout_s: float = 15.0
    max_retries: int = 3

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        """Build settings from ``env`` (defaults to ``os.environ``)."""
        e = os.environ if env is None else env

        def url(key: str) -> str:
            return (e.get(key) or _DEFAULTS[key]).rstrip("/")

        ua = e.get("GEO_USER_AGENT") or (
            f"mcp-geospatial/{__version__} (+https://github.com/ystoneman/geospatial-mcp)"
        )
        cache_dir = Path(e["GEO_CACHE_DIR"]) if e.get("GEO_CACHE_DIR") else default_cache_dir()
        return cls(
            nominatim_url=url("NOMINATIM_URL"),
            photon_url=url("PHOTON_URL"),
            overpass_url=url("OVERPASS_URL"),
            valhalla_url=url("VALHALLA_URL"),
            open_meteo_url=url("OPEN_METEO_BASE_URL"),
            open_meteo_archive_url=url("OPEN_METEO_ARCHIVE_URL"),
            open_meteo_air_quality_url=url("OPEN_METEO_AIR_QUALITY_URL"),
            opentopodata_url=url("OPENTOPODATA_URL"),
            dem_tile_url=e.get("DEM_TILE_URL") or _DEFAULTS["DEM_TILE_URL"],
            beacondb_url=url("BEACONDB_URL"),
            usgs_earthquake_url=url("USGS_EARTHQUAKE_URL"),
            nws_url=url("NWS_URL"),
            celestrak_url=url("CELESTRAK_URL"),
            openrouteservice_key=e.get("OPENROUTESERVICE_API_KEY") or None,
            opencellid_key=e.get("OPENCELLID_API_KEY") or None,
            open_meteo_key=e.get("OPEN_METEO_API_KEY") or None,
            firms_map_key=e.get("FIRMS_MAP_KEY") or None,
            user_agent=ua,
            cache_dir=cache_dir,
            cache_enabled=(e.get("GEO_CACHE", "1").lower() not in {"0", "false", "no"}),
            toolsets=e.get("GEO_TOOLSETS", "default"),
            trace_file=Path(e["GEO_TRACE_FILE"]) if e.get("GEO_TRACE_FILE") else None,
            trace_results=(e.get("GEO_TRACE_RESULTS", "0").lower() in {"1", "true", "yes"}),
            http_timeout_s=float(e.get("GEO_HTTP_TIMEOUT", "15")),
            max_retries=int(e.get("GEO_MAX_RETRIES", "3")),
        )


#: Process-wide settings. Rebind via ``set_settings`` in tests.
settings: Settings = Settings.from_env()


def set_settings(new: Settings) -> None:
    """Replace the process-wide settings (test hook)."""
    global settings
    settings = new
