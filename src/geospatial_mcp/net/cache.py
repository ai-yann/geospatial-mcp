"""On-disk HTTP response cache backed by stdlib sqlite3.

Caching is a policy requirement, not an optimisation. Nominatim's usage policy
explicitly requires that results be cached; Open-Meteo, Overpass and
OpenTopoData all publish daily quotas that a shared egress IP burns through
quickly. A cache also makes the test suite and the eval runs cheap and
repeatable.

TTLs are per-host and reflect how fast the underlying data actually changes:
geocoding results are stable for weeks, a weather forecast for minutes,
elevation effectively forever.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_TTL_S", "TTL_BY_HOST", "ResponseCache"]

#: Seconds a cached response stays fresh, per host.
TTL_BY_HOST: dict[str, float] = {
    "nominatim.openstreetmap.org": 30 * 86400.0,
    "photon.komoot.io": 30 * 86400.0,
    "overpass-api.de": 900.0,
    "valhalla1.openstreetmap.de": 7 * 86400.0,
    "api.openrouteservice.org": 7 * 86400.0,
    # Forecasts. Elevation shares this host and sets its own lifetime per request.
    "api.open-meteo.com": 600.0,
    "archive-api.open-meteo.com": 30 * 86400.0,
    "air-quality-api.open-meteo.com": 1800.0,
    "api.opentopodata.org": 365 * 86400.0,
    "elevation-tiles-prod.s3.amazonaws.com": 365 * 86400.0,
    "earthquake.usgs.gov": 300.0,
    "api.weather.gov": 600.0,
    "celestrak.org": 7200.0,
    "api.beacondb.net": 7 * 86400.0,
    "opencellid.org": 7 * 86400.0,
}

DEFAULT_TTL_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key        TEXT PRIMARY KEY,
    host       TEXT NOT NULL,
    body       BLOB NOT NULL,
    stored_at  REAL NOT NULL,
    expires_at REAL NOT NULL,
    etag       TEXT,
    last_mod   TEXT
);
CREATE INDEX IF NOT EXISTS idx_responses_expiry ON responses(expires_at);
"""


class ResponseCache:
    """A small, thread-safe, self-pruning sqlite response cache."""

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection | None:
        if not self.enabled:
            return None
        if self._conn is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # check_same_thread=False: the SDK runs tools on worker threads
                # and every access is already serialised by self._lock.
                conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
                conn.executescript(_SCHEMA)
                conn.commit()
                self._conn = conn
            except sqlite3.Error:
                # A read-only or full filesystem must degrade to "no cache",
                # never take down a tool call.
                self.enabled = False
                return None
        return self._conn

    @staticmethod
    def make_key(method: str, url: str, params: dict[str, Any] | None, body: Any = None) -> str:
        """Stable cache key from the canonicalised request."""
        payload = json.dumps(
            {
                "m": method.upper(),
                "u": url,
                "p": sorted((params or {}).items(), key=lambda kv: kv[0]),
                "b": body,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def ttl_for(self, host: str) -> float:
        return TTL_BY_HOST.get(host, DEFAULT_TTL_S)

    def get(self, key: str, *, now: float | None = None) -> bytes | None:
        """Return a fresh cached body, or ``None`` if absent or stale."""
        conn = self._connect()
        if conn is None:
            return None
        current = time.time() if now is None else now
        with self._lock:
            try:
                row = conn.execute(
                    "SELECT body, expires_at FROM responses WHERE key = ?", (key,)
                ).fetchone()
            except sqlite3.Error:
                return None
        if row is None or row[1] <= current:
            return None
        return bytes(row[0])

    def set(
        self,
        key: str,
        host: str,
        body: bytes,
        *,
        ttl_s: float | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        now: float | None = None,
    ) -> None:
        """Store a response body under ``key``."""
        conn = self._connect()
        if conn is None:
            return
        current = time.time() if now is None else now
        ttl = self.ttl_for(host) if ttl_s is None else ttl_s
        with self._lock:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO responses"
                    " (key, host, body, stored_at, expires_at, etag, last_mod)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, host, body, current, current + ttl, etag, last_modified),
                )
                conn.commit()
            except sqlite3.Error:
                pass

    def prune(self, *, now: float | None = None) -> int:
        """Delete expired rows; returns how many went."""
        conn = self._connect()
        if conn is None:
            return 0
        current = time.time() if now is None else now
        with self._lock:
            try:
                cur = conn.execute("DELETE FROM responses WHERE expires_at <= ?", (current,))
                conn.commit()
                return cur.rowcount or 0
            except sqlite3.Error:
                return 0

    def clear(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        with self._lock:
            try:
                conn.execute("DELETE FROM responses")
                conn.commit()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
