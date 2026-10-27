"""An opt-in record of every tool call, for evaluating agents in any harness.

Each agent harness logs tool use in its own format, and some barely at all. Set
``GEO_TRACE_FILE`` and the server appends one JSON line per call instead: the
tool, the arguments exactly as the client sent them (before validation, so
malformed calls are recorded too), whether the call failed and why, how long it
took, and how large the result was. Scoring an agent then needs nothing from
the harness except its final answer.

Set ``GEO_TRACE_RESULTS=1`` as well and each successful call also carries a
``preview``: a few headline values from the result, such as a cell radius, an
elevation, the place a name was matched to, the first warning and note, and
whether the answer came from the cache. It is for a person watching the calls
go past, so it is capped at :data:`PREVIEW_LIMIT` characters, long text is cut
between words and marked with an ellipsis, and it never holds geometry.
Scoring does not read it.

The file holds coordinates and search terms, so tracing is never on by default.
It is written to a file, never to stdout, which the stdio transport reserves
for JSON-RPC.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = ["PREVIEW_LIMIT", "enable_tracing"]

log = logging.getLogger(__name__)

_lock = threading.Lock()

#: Maximum length of a preview, as it is written to the trace file.
PREVIEW_LIMIT = 600

#: Longest string field or place name kept in a preview.
_TEXT_LIMIT = 100

#: Longest warning or note kept in a preview. They run to a sentence or two,
#: and the part that says what to do about it tends to come last.
_SENTENCE_LIMIT = 200

#: Marks a string that was cut short.
_ELLIPSIS = "…"

#: Room the place name, warning and note leave for the result's own headline
#: values, such as a cell radius or an area.
_SCALAR_ROOM = 150

#: A warning or note shortened to fit is dropped rather than cut below this.
_SHORTEST_TEXT = 40

#: Nested objects whose scalar fields are headline numbers: area and perimeter
#: from geometry tools, relief and ascent from terrain profiles. ``geom_measure``
#: returns its measurements under ``geometry``; see :func:`_lifted`.
_LIFTED = ("measurements", "statistics", "geometry")

#: Field names that mark text holding geometry in some encoding (WKT, an
#: encoded polyline, the output of a format conversion). Lists and objects are
#: never previewed, so coordinates can only arrive as text like this.
_GEOMETRY_TEXT = ("geometry", "geojson", "polyline", "wkt", "kml", "gpx")


def enable_tracing(server: Any, path: str | Path, *, results: bool = False) -> None:
    """Wrap every registered tool so each call is appended to ``path``.

    ``results`` adds a small preview of each successful result to its record.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    for tool in server._tool_manager.list_tools():
        _wrap(tool, target, results=results)


def _wrap(tool: Any, target: Path, *, results: bool) -> None:
    original = tool.run
    name = tool.name

    async def traced_run(arguments: dict[str, Any], context: Any, **kwargs: Any) -> Any:
        record: dict[str, Any] = {"ts": round(time.time(), 3), "tool": name, "arguments": arguments}
        started = time.monotonic()
        try:
            result = await original(arguments, context, **kwargs)
        except BaseException as exc:
            record.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:1000])
            raise
        else:
            record.update(ok=True, result_chars=_size(result))
            if results:
                record["preview"] = _safe_preview(result)
            return result
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            _append(target, record)

    # The SDK's Tool is a pydantic model; bypass its __setattr__ so the instance
    # attribute shadows the class method the tool manager calls.
    object.__setattr__(tool, "run", traced_run)


def _size(result: Any) -> int | None:
    try:
        dump = getattr(result, "model_dump_json", None)
        return len(dump() if dump else json.dumps(result, default=str))
    except Exception:  # sizing is diagnostic only; never fail a call over it
        return None


def _safe_preview(result: Any) -> dict[str, Any] | None:
    try:
        return _preview(result)
    except Exception:  # a preview is a convenience; never fail a call over it
        return None


def _preview(result: Any) -> dict[str, Any] | None:
    """Pick the headline values out of a tool result, within PREVIEW_LIMIT."""
    data = _structured(result)
    if data is None:
        return None
    scalars: dict[str, Any] = {}
    for key, value in _scalars(data):
        scalars.setdefault(key, value)  # a top-level field wins over a nested one
    extras = _extras(data)
    # Fill in priority order -- the extras are the values a watcher looks for
    # first -- but write the result's own fields first, in the order it has them.
    # A place name, a warning and a note together could fill the preview, so
    # they leave room for a few headline numbers, shortening the last to fit.
    chosen: dict[str, Any] = {}
    for key in sorted(extras, key=lambda k: k != "cached"):  # a flag costs nothing
        value = _fitted(chosen, key, extras[key], PREVIEW_LIMIT - _SCALAR_ROOM)
        if value is not None:
            chosen[key] = value
    for key, value in scalars.items():
        if key not in chosen and _json_len({**chosen, key: value}) <= PREVIEW_LIMIT:
            chosen[key] = value
    order = [*scalars, *(key for key in extras if key not in scalars)]
    return {key: chosen[key] for key in order if key in chosen}


def _fitted(chosen: dict[str, Any], key: str, value: Any, limit: int) -> Any:
    """``value``, shortened if it is text and has to be, so the preview fits ``limit``.

    ``None`` when it cannot fit, or when what would fit is too short to read.
    """
    overflow = _json_len({**chosen, key: value}) - limit
    if overflow <= 0:
        return value
    if not isinstance(value, str):
        return None
    # JSON escapes a non-ASCII character to six, so shorten until it fits.
    room = len(value) - overflow
    while room >= _SHORTEST_TEXT:
        shorter = _clip(value, room)
        if _json_len({**chosen, key: shorter}) <= limit:
            return shorter
        room -= 8
    return None


def _json_len(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _structured(result: Any) -> dict[str, Any] | None:
    """The result as a plain dict, from a CallToolResult or a bare model."""
    if hasattr(result, "structured_content"):
        data = result.structured_content
    elif hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    else:
        data = result
    if not isinstance(data, dict):
        return None
    # The SDK wraps a non-object return value as {"result": ...}.
    if set(data) == {"result"} and isinstance(data["result"], dict):
        data = data["result"]
    return data


def _scalars(data: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Top-level scalar fields, then those of the nested headline objects."""
    for key, value in data.items():
        if key != "meta" and _is_scalar(key, value):
            yield key, _clip(value)
    for name in _LIFTED:
        nested = _lifted(data.get(name))
        for key, value in nested.items():
            if _is_scalar(key, value):
                yield key, _clip(value)


def _lifted(value: Any) -> dict[str, Any]:
    # A GeoJSON object always has a "type"; a dict of measurements never does.
    # Only scalars are taken either way, so coordinates cannot get through.
    if isinstance(value, dict) and "type" not in value:
        return value
    return {}


def _extras(data: dict[str, Any]) -> dict[str, Any]:
    """The values worth seeing that are not top-level scalars."""
    extras: dict[str, Any] = {}
    matches = data.get("matches")
    if isinstance(matches, list) and matches and isinstance(matches[0], dict):
        name = matches[0].get("display_name")
        if isinstance(name, str):
            extras["match"] = _clip(name)
    warnings = data.get("warnings")
    if isinstance(warnings, list) and warnings and isinstance(warnings[0], str):
        extras["warning"] = _clip(warnings[0], _SENTENCE_LIMIT)
    sightline = data.get("line_of_sight")
    if isinstance(sightline, dict):
        extras["los_clear"] = sightline.get("clear")
        if "fresnel_60_percent_clear" in sightline:
            extras["fresnel_60_percent_clear"] = sightline["fresnel_60_percent_clear"]
        worst = sightline.get("worst_point")
        if isinstance(worst, dict):
            extras["worst_distance_m"] = worst.get("distance_m")
            extras["worst_clearance_m"] = worst.get("clearance_m")
    meta = data.get("meta")
    if isinstance(meta, dict):
        notes = meta.get("notes")
        if isinstance(notes, list) and notes and isinstance(notes[0], str):
            extras["note"] = _clip(notes[0], _SENTENCE_LIMIT)
        if isinstance(meta.get("cached"), bool):
            extras["cached"] = meta["cached"]
    return extras


def _is_scalar(key: str, value: Any) -> bool:
    if isinstance(value, str):
        return not _is_geometry_text(key)
    return isinstance(value, (bool, int, float))


def _is_geometry_text(key: str) -> bool:
    # data_convert's converted document is in "output", whatever its format.
    return key == "output" or any(marker in key.lower() for marker in _GEOMETRY_TEXT)


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> Any:
    """A string of at most ``limit`` characters, cut between words with an ellipsis.

    A cut mid-word reads as a typo ("(1500-2000); tr") and hides where the
    sentence was going. When no space falls in the second half of the string,
    as in a long file name or URL, the cut is made at the limit instead. A cut
    after a full stop keeps it, and a space, before the ellipsis.
    """
    if not isinstance(value, str) or len(value) <= limit:
        return value
    room = limit - len(" " + _ELLIPSIS)
    head = value[: room + 1]  # one more character, to see whether a word ends at `room`
    space = head.rfind(" ")
    head = head[:space] if space > limit // 2 else head[:room]
    head = head.rstrip(" ,;:-(")
    return head + (" " if head.endswith(".") else "") + _ELLIPSIS


def _append(target: Path, record: dict[str, Any]) -> None:
    line = json.dumps(record, default=str) + "\n"
    try:
        with _lock, target.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:  # a full disk must not turn a good answer into an error
        log.warning("could not write tool trace to %s: %s", target, exc)
