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
go past, so it is capped at :data:`PREVIEW_LIMIT` characters and never holds
geometry. Scoring does not read it.

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
PREVIEW_LIMIT = 500

#: Longest string kept in a preview. Notes and place names run to a sentence.
_TEXT_LIMIT = 100

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
    chosen: dict[str, Any] = {}
    for key, value in [*extras.items(), *scalars.items()]:
        if key in chosen:
            continue
        trial = {**chosen, key: value}
        if len(json.dumps(trial, default=str)) <= PREVIEW_LIMIT:
            chosen = trial
    order = [*scalars, *(key for key in extras if key not in scalars)]
    return {key: chosen[key] for key in order if key in chosen}


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
        extras["warning"] = _clip(warnings[0])
    meta = data.get("meta")
    if isinstance(meta, dict):
        notes = meta.get("notes")
        if isinstance(notes, list) and notes and isinstance(notes[0], str):
            extras["note"] = _clip(notes[0])
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


def _clip(value: Any) -> Any:
    return value[:_TEXT_LIMIT] if isinstance(value, str) else value


def _append(target: Path, record: dict[str, Any]) -> None:
    line = json.dumps(record, default=str) + "\n"
    try:
        with _lock, target.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:  # a full disk must not turn a good answer into an error
        log.warning("could not write tool trace to %s: %s", target, exc)
