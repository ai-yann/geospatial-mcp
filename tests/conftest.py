"""Shared fixtures, and a guard against accidental network access.

A test that reaches the network without opting in fails loudly rather than
flaking. A forgotten mock is a bug, and a suite that quietly depends on a free
public API will break in CI at the worst possible moment.

The block is applied at ``connect`` rather than at socket construction, and
only for non-loopback addresses. Blocking the constructor outright also breaks
``socket.socketpair()``, which asyncio uses for the event loop's internal
self-pipe -- so every async test would fail for reasons unrelated to the
network.

The guard only sees Python's ``socket`` module. A C extension with its own
networking goes around it -- notably PROJ, which fetches datum grids through
libcurl when ``PROJ_NETWORK`` is on. Never enable PROJ networking in-process in
a test; see ``tests/geo/test_crs.py`` for the subprocess pattern instead.
"""

from __future__ import annotations

import ipaddress
import socket
from contextlib import asynccontextmanager

import pytest

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE_CONNECTION = socket.create_connection


def _is_local(address: object) -> bool:
    """True for loopback and unix-socket addresses, which are always allowed."""
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX and friends
    host = address[0]
    if not isinstance(host, str):
        return True
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _blocked(target: object) -> RuntimeError:
    return RuntimeError(
        f"This test tried to open a network connection to {target!r}. Tests run "
        "offline by default. Either mock the provider, or mark the test with "
        "@pytest.mark.network (excluded from the default run)."
    )


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        return

    def guarded_connect(self, address):
        if not _is_local(address):
            raise _blocked(address)
        return _REAL_CONNECT(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            raise _blocked(address)
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture
def server():
    """A server with the default toolsets registered."""
    from geospatial_mcp.server import build_server

    return build_server("default")


@asynccontextmanager
async def mcp_client(toolsets: str = "default"):
    """An in-process MCP client wired straight to the server object.

    No transport and no subprocess: the SDK connects client to server in
    memory, which is what makes these contract tests fast enough to run on
    every commit.

    Deliberately a context manager rather than a pytest fixture. An async
    generator fixture has its setup and teardown run in different asyncio
    tasks, and anyio's cancel scopes -- which the MCP session uses -- refuse to
    be exited from a task other than the one that entered them. Entering the
    client inside the test body keeps it all on one task.
    """
    from mcp import Client

    from geospatial_mcp.server import build_server

    async with Client(build_server(toolsets), raise_exceptions=False) as connected:
        yield connected
