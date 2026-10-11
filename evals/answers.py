"""Deterministic checks on an agent's final answer and on files it wrote.

No model grades these. Each case states what a correct answer must contain --
a pattern, or a number within a tolerance -- and what it must not, so a score
cannot drift the way an LLM judge's can. The numeric questions this server
exists to answer suit that well. Rubrics in the library stay for human review.

A case opts in with an ``answer`` block::

    answer:
      patterns: ["Palacio de Congresos"]   # regexes, case-insensitive, all required
      numbers:  [{near: 57.4, tol: 3}]     # each needs some number within tol
      forbid:   ["there are no towers"]    # regexes that must not match

and, for harnesses that can write files, an ``artifacts`` block::

    artifacts:
      - file: zone.geojson
        area_km2: {near: 176.7, tol: 3}    # geodesic area of the geometry
        aspect: {near: 1.0, tol: 0.05}     # east-west over north-south extent
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = ["check_answer", "check_artifacts", "numbers_in"]

#: A thousands separator: a comma followed by exactly three digits and no more.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every number in ``text``, reading ``7,500`` as 7500 and ``57.4%`` as 57.4."""
    return [float(n) for n in _NUMBER.findall(_THOUSANDS.sub("", text))]


def check_answer(spec: dict[str, Any] | None, text: str) -> tuple[bool | None, str]:
    """Score ``text`` against a case's ``answer`` block; ``None`` means unscored."""
    if not spec:
        return None, ""
    problems: list[str] = []
    for pattern in spec.get("patterns", []):
        if not re.search(pattern, text, re.IGNORECASE):
            problems.append(f"missing /{pattern}/")
    for pattern in spec.get("forbid", []):
        if re.search(pattern, text, re.IGNORECASE):
            problems.append(f"says /{pattern}/")
    found = numbers_in(text)
    for want in spec.get("numbers", []):
        near, tol = float(want["near"]), float(want["tol"])
        if not any(abs(n - near) <= tol for n in found):
            problems.append(f"no number within {tol:g} of {near:g}")
    return not problems, "; ".join(problems)


def check_artifacts(specs: list[dict[str, Any]] | None, workdir: Path) -> tuple[bool | None, str]:
    """Measure files the agent wrote, with this server's own geodesic functions."""
    if not specs:
        return None, ""
    from shapely.geometry import shape

    from geospatial_mcp.geo import geodesy
    from geospatial_mcp.geo.geometry import measure_geometry

    problems: list[str] = []
    for spec in specs:
        path = workdir / spec["file"]
        if not path.is_file():
            problems.append(f"{spec['file']} was not written")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            geometry = shape(_first_geometry(data))
        except Exception as exc:
            problems.append(f"{spec['file']} is not readable GeoJSON: {exc}")
            continue
        measured = measure_geometry(geometry)
        if "area_km2" in spec:
            want = spec["area_km2"]
            area = float(measured["area_km2"])
            if abs(area - want["near"]) > want["tol"]:
                problems.append(f"area {area:.1f} km2, expected {want['near']:g}±{want['tol']:g}")
        if "aspect" in spec:
            box = measured["bbox"]
            mid_lat = (box["south"] + box["north"]) / 2
            mid_lon = (box["west"] + box["east"]) / 2
            width = geodesy.distance_m(mid_lat, box["west"], mid_lat, box["east"])
            height = geodesy.distance_m(box["south"], mid_lon, box["north"], mid_lon)
            aspect = width / height if height else float("inf")
            want = spec["aspect"]
            if abs(aspect - want["near"]) > want["tol"]:
                problems.append(f"east-west/north-south {aspect:.2f}, expected {want['near']:g}")
    return not problems, "; ".join(problems)


def _first_geometry(data: dict[str, Any]) -> dict[str, Any]:
    """Accept a bare geometry, a Feature, or the first Feature of a collection."""
    if data.get("type") == "FeatureCollection":
        return _first_geometry(data["features"][0])
    if data.get("type") == "Feature":
        return data["geometry"]
    return data
