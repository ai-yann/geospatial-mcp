"""Deterministic checks on an agent's final answer and on files it wrote.

No model grades these. Each case states what a correct answer must contain --
a pattern, or a number within a tolerance -- and what it must not, so a score
cannot drift the way an LLM judge's can. The numeric questions this server
exists to answer suit that well. Rubrics in the library stay for human review.

A case opts in with an ``answer`` block::

    answer:
      patterns: ["Palacio de Congresos"]   # regexes, case-insensitive, all required
      numbers:  [{near: 129.1, tol: 3, unit: km2, about: "total|four"}]
      forbid:   ["there are no towers"]    # regexes that must not match

and, for harnesses that can write files, an ``artifacts`` block::

    artifacts:
      - file: zone.geojson
        area_km2: {near: 176.7, tol: 3}    # geodesic area of the geometry
        aspect: {near: 1.0, tol: 0.05}     # east-west over north-south extent

Each entry in ``numbers`` is checked against the answer's *headline* figure for
that quantity (see :func:`headline`), not against every number in the text.
Until October 2026 any number within tolerance passed, so an answer that led
with the wrong figure passed on a right one in an aside: "**≈ 107 km²** ...
**106.8 km²** (If you instead count each cell's full circular footprint ...
≈ 128.7 km².)" passed a check for 129.1 ± 3.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["UNITS", "check_answer", "check_artifacts", "headline", "numbers_in"]

#: A thousands separator: a comma followed by exactly three digits and no more.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

#: The units a ``numbers`` entry can name, as they are written after a figure.
#: "km" is not "km²" or "km/h", and "m" is not "m²", "m/s", "mm", "MHz" or the
#: "m" of "90 m-resolution".
UNITS: dict[str, str] = {
    "km2": r"(?:km²|km2|km\^2|sq\.?\s?km|square\s+kilomet(?:re|er)s?)",
    "km": r"(?:km|kilomet(?:re|er)s?)(?![²2^/\w-])",
    "m": r"(?:m|metres?|meters?)(?![²2^/\w-])",
    "%": r"(?:%|per\s?cent)",
}

#: How far, in characters on the same line, an ``about`` word may sit from its number.
ABOUT_REACH = 60

#: A figure on its own: not the tail of a word or of another number ("km2", "3.21").
_FIGURE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
#: A figure that is one side of a multiplication or division is working, not a result:
#: the 26.7 in four times 26.7 km², the 7.5 in "7.5 km / √2", either 7.5 in 7.5 by 7.5 km.
_OPERATOR_BEFORE = re.compile(r"(?:[×·÷/]|\bx)\s*$")
_OPERATOR_AFTER = re.compile(r"\s*(?:[×·÷/]|x\b)")
#: ...except a slash between two figures that each carry a unit, which gives the same quantity twice
#: ("342 m / 1,122 ft", "5 km / 3.1 mi"), not a division.
_UNIT_WORD = r"(?:km²|km2|km|m²|m|ft|feet|mi|miles?|metres?|meters?|%|°[CF]?)"
_ALSO_BEFORE = re.compile(r"\d[\d,.]*\s*" + _UNIT_WORD + r"\s*/\s*$")
_ALSO_AFTER = re.compile(r"\s*/\s*\d[\d,.]*\s*" + _UNIT_WORD + r"(?!\w)")
_BOLD = "**"
#: Bold and spacing as models write them in LaTeX, as Codex sometimes does.
_LATEX = (
    (re.compile(r"\\(?:mathbf|textbf|boldsymbol)\{([^{}]*)\}"), r"**\1**"),
    (re.compile(r"\\(?:text|mathrm|operatorname)\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\approx\b"), "≈"),
    (re.compile(r"\\times\b"), "×"),
    (re.compile(r"\\[ ,;:!]"), " "),
)


def numbers_in(text: str) -> list[float]:
    """Every number in ``text``, reading ``7,500`` as 7500 and ``57.4%`` as 57.4."""
    return [float(n) for n in _NUMBER.findall(_THOUSANDS.sub("", text))]


def headline(text: str, want: Mapping[str, Any]) -> float | None:
    """The figure ``text`` gives for the quantity ``want`` describes, or ``None``.

    Candidates are the numbers written in ``want["unit"]`` (any number if it
    names none), leaving out the operands of a multiplication or division. If
    ``want["about"]`` is set, a candidate also needs one of its words (a regex)
    to be nearer to it than to any other candidate on the same line, and at most
    :data:`ABOUT_REACH` characters away: that tells a total from the per-cell
    figure beside it, or an elevation from the height above a river.

    The headline is the first candidate the answer puts in bold, where models
    put the figure they settle on; if none is in bold, the first candidate.
    """
    unit = want.get("unit")
    if unit and unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}: use one of {', '.join(UNITS)}")
    unit_after = re.compile(r"\s?" + UNITS[unit], re.IGNORECASE) if unit else None
    about = re.compile(want["about"], re.IGNORECASE) if want.get("about") else None
    plain, bold = _unmark(text)
    first: float | None = None
    offset = 0
    for line in plain.split("\n"):
        figures = _figures(line, unit_after)
        if about and figures:
            figures = _nearest_to(about, line, figures)
        for start, _end, value in figures:
            if bold[offset + start]:
                return value
            if first is None:
                first = value
        offset += len(line) + 1
    return first


def _unmark(text: str) -> tuple[str, list[bool]]:
    """``text`` without ``**`` markers, backticks or thousands separators, and for
    each of its characters whether it was inside ``**bold**``."""
    for pattern, replacement in _LATEX:
        text = pattern.sub(replacement, text)
    text = _THOUSANDS.sub("", text).replace("`", "")
    plain: list[str] = []
    bold: list[bool] = []
    inside = False
    i = 0
    while i < len(text):
        if text.startswith(_BOLD, i):
            inside = not inside
            i += len(_BOLD)
            continue
        plain.append(text[i])
        bold.append(inside)
        i += 1
    return "".join(plain), bold


def _figures(line: str, unit_after: re.Pattern[str] | None) -> list[tuple[int, int, float]]:
    """(start, end, value) of each candidate figure in ``line``, its unit included in the span."""
    found = []
    for match in _FIGURE.finditer(line):
        end = match.end()
        if unit_after:
            unit = unit_after.match(line, end)
            if not unit:
                continue
            end = unit.end()
        before = _OPERATOR_BEFORE.search(line, 0, match.start()) and not _ALSO_BEFORE.search(
            line, 0, match.start()
        )
        after = _OPERATOR_AFTER.match(line, end) and not (
            unit_after and _ALSO_AFTER.match(line, end)
        )
        if before or after:
            continue
        found.append((match.start(), end, float(match.group())))
    return found


def _gap(figure: tuple[int, int, float], word: re.Match[str]) -> int:
    """Characters between a figure and a word on the same line; 0 if they overlap."""
    return max(figure[0] - word.end(), word.start() - figure[1], 0)


def _nearest_to(
    about: re.Pattern[str], line: str, figures: list[tuple[int, int, float]]
) -> list[tuple[int, int, float]]:
    """The figures that are the nearest one to some ``about`` word, within reach."""
    keep: set[int] = set()
    for word in about.finditer(line):
        nearest = min(range(len(figures)), key=lambda i: _gap(figures[i], word))
        if _gap(figures[nearest], word) <= ABOUT_REACH:
            keep.add(nearest)
    return [figure for i, figure in enumerate(figures) if i in keep]


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
    for want in spec.get("numbers", []):
        near, tol = float(want["near"]), float(want["tol"])
        unit = f" {want['unit']}" if want.get("unit") else ""
        value = headline(text, want)
        if value is None:
            about = f" near /{want['about']}/" if want.get("about") else ""
            problems.append(
                f"no figure in{unit or ' the answer'}{about}, expected {near:g}±{tol:g}"
            )
        elif abs(value - near) > tol:
            problems.append(f"headline {value:g}{unit}, expected {near:g}±{tol:g}")
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
