"""Server assembly. Wiring only -- no tool logic lives here."""

from __future__ import annotations

import logging
import sys
from typing import Any

from . import __version__
from ._sdk import MCPServer
from .schema import slim_server_schemas
from .tools import drop_network_tools, register_toolsets, resolve_toolsets

__all__ = ["INSTRUCTIONS", "build_server", "configure_logging"]

#: Sent to the model on connect. Kept under 2 KB: Claude Code truncates there.
INSTRUCTIONS = """\
Geospatial toolkit: coordinates, geocoding, routing, terrain, weather and radio links.

Any tool taking a `location` accepts decimal degrees ("48.8584,2.2945"), DMS, \
a USNG/MGRS grid reference, geohash, H3, a Plus Code, or a place name to geocode. \
Decimal degrees are canonical; latitude comes first.

Picking the right tool:
- Straight-line distance -> geom_measure. Travel distance and time -> route_directions.
- Travel times between many places -> route_matrix, one call, not repeated route_directions.
- Everywhere reachable in N minutes -> route_isochrone.
- Ground height along a path -> terrain_profile. At one point -> terrain_elevation.
- One named place -> place_geocode. A category of place nearby -> place_search.
- Coordinate notation change -> coord_convert. Datum or projection change -> \
coord_transform_crs.

Results carry a `meta` block with the data source and its required attribution; \
pass that through when showing results to a user. `meta.notes` carries accuracy \
caveats worth repeating.

If a capability seems missing, call geo_capabilities before concluding it is \
unavailable -- it may be in a toolset that is switched off.
"""


def configure_logging(level: str = "INFO") -> None:
    """Send all logging to stderr.

    This is a correctness requirement, not a preference. The MCP stdio transport
    reserves stdout for JSON-RPC frames: "The server MUST NOT write anything to
    its stdout that is not a valid MCP message." A single log line on stdout
    corrupts the stream and the client disconnects. Note how easy the mistake is
    to make and how hard to notice: ``stream=sys.stdout`` is a one-word change
    that leaves the HTTP transport working perfectly while making stdio unusable.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    # httpx logs every request line at INFO, including the full query string.
    # Our query strings contain the user's coordinates and search terms, so
    # this would write location data into any log the operator collects.
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _name_rate_limited_tools(server: MCPServer) -> None:
    """Put each tool's name into the rate-limit errors raised while it runs.

    The SDK wraps whatever a tool raises as ``Error executing tool <name>: <message>``
    before the model sees it, so the tool's own message is rebuilt here, from the
    original exception, with the name filled in.
    """
    from ._sdk import ToolError
    from .errors import GeoRateLimited

    for tool in server._tool_manager.list_tools():
        original, name = tool.run, tool.name

        async def run(*args: Any, _run: Any = original, _name: str = name, **kwargs: Any) -> Any:
            try:
                return await _run(*args, **kwargs)
            except ToolError as exc:
                cause = exc.__cause__
                if not isinstance(cause, GeoRateLimited):
                    raise
                cause.for_tool(_name)
                raise ToolError(f"Error executing tool {_name}: {cause}") from cause

        # The SDK's Tool is a pydantic model; bypass its __setattr__ (as trace.py does).
        object.__setattr__(tool, "run", run)


def build_server(toolsets: str | set[str] | None = None) -> MCPServer:
    """Build a server with the requested toolsets registered."""
    requested = toolsets if isinstance(toolsets, str) else None
    names = toolsets if isinstance(toolsets, set) else resolve_toolsets(toolsets)
    offline_only = bool(requested and "offline" in requested.split(","))
    mcp = MCPServer(
        name="geospatial",
        title="Geospatial Toolkit",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/ystoneman/geospatial-mcp",
    )
    # An alias means "everything available"; a named toolset means "this one",
    # so a missing extra is only fatal in the latter case.
    broad = bool(requested and {"all", "offline"} & set(requested.split(",")))
    registered = register_toolsets(mcp, names, skip_unavailable=broad)
    if offline_only:
        drop_network_tools(mcp)

    # Generated schemas carry a lot of redundancy that costs context on every
    # turn. See geospatial_mcp.schema for what is removed and why.
    slim_server_schemas(mcp)

    # A rate-limit error names the call to make again. See errors.GeoRateLimited.
    _name_rate_limited_tools(mcp)

    # Opt-in: one JSON line per tool call, for evaluating agents in any harness.
    from .config import settings

    if settings.trace_file:
        from .trace import enable_tracing

        enable_tracing(mcp, settings.trace_file, results=settings.trace_results)

    from .tools.core import set_enabled_toolsets

    set_enabled_toolsets(registered)
    return mcp
