"""The only module that imports from the MCP SDK.

Every other module in this package imports MCP types from here. When the SDK
changes shape -- as it did between 1.x (``mcp.server.fastmcp.FastMCP``) and
2.x (``mcp.server.MCPServer``) -- exactly one file needs editing.

Do not ``import mcp`` anywhere else. ``tests/mcp/test_sdk_isolation.py``
enforces this.
"""

from __future__ import annotations

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

__all__ = [
    "MCPError",
    "MCPServer",
    "ToolAnnotations",
    "ToolError",
    "compute",
    "network",
    "read_only",
]


def read_only(*, open_world: bool) -> ToolAnnotations:
    """Annotations for a tool that never mutates state.

    Every tool in this package is read-only; ``open_world`` distinguishes pure
    computation (False) from calls that reach an external service (True).
    """
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=open_world,
    )


#: Annotations for offline, deterministic tools (pure math, no I/O).
def compute() -> ToolAnnotations:
    return read_only(open_world=False)


#: Annotations for tools that call an external provider.
def network() -> ToolAnnotations:
    return read_only(open_world=True)
