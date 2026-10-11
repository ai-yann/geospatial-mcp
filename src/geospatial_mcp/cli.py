"""Command-line entry point.

``print`` is used deliberately here (and nowhere else in the package): the CLI
writes its ``--list-tools`` output and argument errors to the console for a
human. Once a transport starts, nothing in this package writes to stdout except
the MCP protocol itself -- see ``server.configure_logging``.
"""
# ruff: noqa: T201

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__

__all__ = ["build_parser", "main"]

logger = logging.getLogger("geospatial_mcp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-geospatial",
        description=(
            "Geospatial MCP server: coordinates, geocoding, routing, terrain, "
            "weather and radio propagation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  mcp-geospatial                          # stdio, default toolsets\n"
            "  mcp-geospatial --toolsets all           # every tool\n"
            "  mcp-geospatial --transport http --port 8000\n"
            "  mcp-geospatial --list-tools             # print the tool catalogue and exit\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"mcp-geospatial {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default) works with every MCP client; http serves streamable HTTP.",
    )
    parser.add_argument(
        "--toolsets",
        default=None,
        help="Comma-separated toolsets, or an alias: default, all, offline. "
        "Overrides GEO_TOOLSETS.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address.")
    parser.add_argument(
        "--port", type=int, default=None, help="HTTP port (default 8000, or $PORT)."
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("GEO_LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the registered tools as JSON and exit, without starting a server.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from .config import Settings, set_settings
    from .server import build_server, configure_logging

    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    set_settings(Settings.from_env())

    toolsets = args.toolsets or os.environ.get("GEO_TOOLSETS") or "default"
    try:
        server = build_server(toolsets)
    except (ValueError, RuntimeError) as exc:
        # ValueError: an unknown toolset name.
        # RuntimeError: a known toolset whose optional extra is not installed.
        # Both carry a message the operator can act on, so print it plainly
        # rather than dumping a traceback at them.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_tools:
        import asyncio
        import json

        tools = asyncio.run(server.list_tools())
        print(
            json.dumps(
                [
                    {
                        "name": t.name,
                        "description": (t.description or "").split("\n")[0],
                        "parameters": sorted((t.input_schema.get("properties") or {}).keys()),
                    }
                    for t in tools
                ],
                indent=2,
            )
        )
        return 0

    if args.transport == "stdio":
        logger.info("mcp-geospatial %s starting on stdio (toolsets: %s)", __version__, toolsets)
        server.run(transport="stdio")
        return 0

    port = args.port or int(os.environ.get("PORT", "8000"))
    # Bind to all interfaces only when a platform explicitly assigns a port,
    # which is the signal that we are inside a managed container.
    host = args.host if "PORT" not in os.environ else "0.0.0.0"
    logger.info("mcp-geospatial %s starting on http://%s:%d/mcp", __version__, host, port)
    server.settings.host = host  # type: ignore[attr-defined]
    server.settings.port = port  # type: ignore[attr-defined]
    server.run(transport="streamable-http")
    return 0
