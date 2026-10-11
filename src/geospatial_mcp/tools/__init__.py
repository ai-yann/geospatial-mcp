"""Toolset registry.

Tools are grouped into toolsets that can be enabled independently. This exists
for one reason: every tool definition costs context in the model's window on
every single turn, whether or not it is used. A server that exposes fifty tools
by default is quietly taxing every conversation.

The default set (``core`` + ``rf``) is 22 tools, roughly 12k tokens of schema.
Everything else is opt-in via ``GEO_TOOLSETS`` or ``--toolsets``.

Selection follows the pattern the GitHub MCP server established::

    GEO_TOOLSETS=default          core + rf                    (22 tools)
    GEO_TOOLSETS=all              every toolset
    GEO_TOOLSETS=offline          every tool that needs no network
    GEO_TOOLSETS=core,analysis    an explicit list
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import install_commands

if TYPE_CHECKING:  # pragma: no cover
    from .._sdk import MCPServer

logger = logging.getLogger("geospatial_mcp.tools")

__all__ = [
    "ALIASES",
    "TOOLSETS",
    "Toolset",
    "drop_network_tools",
    "register_toolsets",
    "resolve_toolsets",
]


@dataclass(frozen=True)
class Toolset:
    """One group of related tools."""

    name: str
    summary: str
    register: Callable[[MCPServer], None]
    default_on: bool = False
    #: Optional-dependency extra this toolset needs, if any.
    extra: str | None = None
    #: Module to import as proof that ``extra`` is installed.
    #:
    #: Needed because tool modules import heavy optional dependencies lazily,
    #: inside the function body, so registration succeeds whether or not the
    #: extra is present -- and the failure only surfaces when a user calls the
    #: tool. Probing here turns that into a startup error with an install
    #: command, which is what the operator can act on.
    probe_module: str | None = None
    #: True when every tool in the set works with no network access. Note the
    #: ``offline`` alias does not rely on this: ``core`` and ``rf`` each mix
    #: offline and networked tools, so filtering at toolset granularity would
    #: discard most of what works offline. See :func:`drop_network_tools`.
    offline: bool = False


def _lazy(module_name: str) -> Callable[[MCPServer], None]:
    """Defer importing a tool module until its toolset is actually enabled.

    Keeps startup fast and means a missing optional dependency surfaces only
    when the toolset needing it is requested.
    """

    def register(mcp: MCPServer) -> None:
        from importlib import import_module

        module = import_module(f".{module_name}", __package__)
        module.register(mcp)

    return register


TOOLSETS: dict[str, Toolset] = {
    "core": Toolset(
        name="core",
        summary=(
            "Coordinates, geometry, geocoding, routing, terrain, weather, sun "
            "and data formats. The general-purpose set."
        ),
        register=_lazy("core"),
        default_on=True,
    ),
    "analysis": Toolset(
        name="analysis",
        summary=(
            "Spatial clustering, density and grid binning: find where points "
            "concentrate, or aggregate them into H3 or geohash cells."
        ),
        register=_lazy("analysis"),
        default_on=False,
        extra="stats",
        probe_module="sklearn",
        offline=True,
    ),
    "rf": Toolset(
        name="rf",
        summary=(
            "Radio link budgets and cell-tower lookup: how far a transmitter "
            "reaches, and what is already on the air nearby."
        ),
        register=_lazy("rf"),
        default_on=True,
    ),
}

#: Named collections users can request instead of listing toolsets.
ALIASES = ("default", "all", "offline")


def resolve_toolsets(spec: str | Iterable[str] | None) -> set[str]:
    """Turn a toolset specification into a concrete set of names.

    Raises ``ValueError`` naming the valid options when given an unknown name,
    rather than silently enabling nothing.
    """
    if spec is None:
        spec = "default"
    names = (
        [part.strip() for part in spec.split(",") if part.strip()]
        if isinstance(spec, str)
        else [str(part).strip() for part in spec if str(part).strip()]
    )
    if not names:
        names = ["default"]

    selected: set[str] = set()
    for name in names:
        if name == "default":
            selected |= {n for n, ts in TOOLSETS.items() if ts.default_on}
        elif name == "all":
            selected |= set(TOOLSETS)
        elif name == "offline":
            # Every toolset; individual networked tools are filtered out after
            # registration by drop_network_tools().
            selected |= set(TOOLSETS)
        elif name in TOOLSETS:
            selected.add(name)
        else:
            valid = ", ".join(sorted(TOOLSETS) + list(ALIASES))
            raise ValueError(f"Unknown toolset {name!r}. Valid toolsets and aliases: {valid}.")
    return selected


def _require_extra(toolset: Toolset) -> None:
    """Fail at startup if a toolset's optional dependency is missing.

    Registration alone does not prove a toolset is usable: tool modules import
    heavy optional dependencies lazily inside the function body, so a toolset
    whose extra is absent registers cleanly and then fails on first use, deep
    inside a conversation. Probing the import here surfaces it immediately,
    with the command that fixes it.
    """
    if not toolset.probe_module:
        return
    from importlib.util import find_spec

    try:
        found = find_spec(toolset.probe_module) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        uvx, pip = install_commands(toolset.extra or "")
        raise RuntimeError(
            f"Toolset {toolset.name!r} needs the optional '{toolset.extra}' extra, "
            f"which is not installed (missing module: {toolset.probe_module}).\n"
            f"Install it with:\n  {uvx}\nor:\n  {pip}"
        )


def drop_network_tools(mcp: MCPServer) -> list[str]:
    """Remove every registered tool that reaches an external service.

    Backs the ``offline`` alias. Filtering has to happen per tool rather than
    per toolset because the useful toolsets are mixed: ``core`` holds both
    ``coord_convert`` (pure maths) and ``place_geocode`` (a live API call).
    Selecting only wholly-offline toolsets would discard the former along with
    the latter, leaving almost nothing.

    Tools are identified by the ``openWorldHint`` annotation they already
    declare, so there is no second list to keep in sync.
    """
    removed: list[str] = []
    for tool in list(mcp._tool_manager.list_tools()):
        annotations = getattr(tool, "annotations", None)
        if annotations is not None and getattr(annotations, "open_world_hint", False):
            mcp.remove_tool(tool.name)
            removed.append(tool.name)
    return removed


def register_toolsets(
    mcp: MCPServer, names: Iterable[str], *, skip_unavailable: bool = False
) -> list[str]:
    """Register the named toolsets on a server. Returns the names registered.

    ``skip_unavailable`` quietly drops toolsets whose optional dependency is
    missing. Used for the ``all`` and ``offline`` aliases, where the user asked
    for "everything available" rather than for one specific toolset -- failing
    the whole server because one optional extra is absent would be unhelpful.
    An explicitly named toolset always raises.
    """
    registered: list[str] = []
    for name in sorted(names):
        toolset = TOOLSETS.get(name)
        if toolset is None:
            continue
        try:
            _require_extra(toolset)
        except RuntimeError:
            if skip_unavailable:
                logger.info(
                    "Skipping toolset %r: optional extra %r is not installed.",
                    name,
                    toolset.extra,
                )
                continue
            raise
        try:
            toolset.register(mcp)
        except ImportError as exc:
            # Fail loudly at startup with the exact install command, rather
            # than registering a tool that raises the first time it is called.
            uvx, pip = install_commands(toolset.extra or "")
            raise RuntimeError(
                f"Toolset {name!r} needs the optional '{toolset.extra}' extra, which "
                f"is not installed ({exc}). Install it with:\n  {uvx}\nor:\n  {pip}"
            ) from exc
        registered.append(name)
    return registered
