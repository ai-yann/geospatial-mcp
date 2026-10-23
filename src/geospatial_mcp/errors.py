"""Error types.

Design rule, and the single most important behavioural contract in this
package: **tools raise, they never return an error payload.**

Returning ``{"status": "error", ...}`` from a failed tool is the tempting shape
and the wrong one. MCP marks such a response ``is_error=False``, so the model
reads the failure as a success and proceeds on made-up data. Raising sets
``is_error=True`` and puts the message in front of the model, which can then
correct itself.

Only if the exception is a ``ToolError``, though. From ``mcp`` 2.1 the SDK
treats any other exception from a tool as a crash: the model is told only
``Error executing tool <name>``, and the message stays in the server log. So
:class:`GeoError` subclasses the SDK's ``ToolError``. Without that, every
message below, each written to teach the model a fix, would reach it as that
one generic line. ``tests/mcp/test_error_quality.py`` checks the base class.

Two tiers:

* :class:`GeoInputError` and its subclasses -- the model can plausibly recover.
  The message reaches the model, so it must say what was received, why that is
  wrong, and show a valid example.
* :class:`GeoConfigError` -- operator misconfiguration the model cannot fix
  (missing API key, bad base URL). The message still reaches the model, so it
  can tell the user what the operator has to change.
"""

from __future__ import annotations

from ._sdk import ToolError

__all__ = [
    "GeoConfigError",
    "GeoError",
    "GeoInputError",
    "GeoNoResults",
    "GeoRateLimited",
    "GeoUnavailable",
    "GeoUpstreamError",
    "install_commands",
]

#: Where the server installs from. It is not on PyPI, and the PyPI package named
#: ``geospatial-mcp`` is an unrelated project, so an install command that gives
#: only a package name would fail or fetch someone else's code.
INSTALL_SOURCE = "git+https://github.com/ystoneman/geospatial-mcp"


def install_commands(extra: str) -> tuple[str, str]:
    """The ``uvx`` and ``pip`` commands that install this server with ``extra``."""
    requirement = f"'mcp-geospatial[{extra}] @ {INSTALL_SOURCE}'"
    return f"uvx --from {requirement} mcp-geospatial", f"pip install {requirement}"


class GeoError(ToolError):
    """Base class for every error this package raises deliberately.

    A ``ToolError``, so the SDK passes the message to the model rather than
    replacing it with a generic one. See the module docstring.
    """


class GeoInputError(GeoError, ValueError):
    """The caller supplied something invalid, and could retry successfully.

    Always construct with :meth:`with_example` where a concrete example exists.
    ``tests/mcp/test_error_quality.py`` asserts that every input error message
    a tool can produce contains a usable example.
    """

    @classmethod
    def with_example(cls, *, got: object, problem: str, example: str) -> GeoInputError:
        """Build a message that names the input, the problem, and a fix."""
        return cls(f"{problem} Received: {got!r}. Example of a valid value: {example}")


class GeoConfigError(GeoError):
    """The server is misconfigured; the model cannot fix this by retrying."""


class GeoUpstreamError(GeoError):
    """An upstream provider returned an error or an unparseable response."""


class GeoRateLimited(GeoUpstreamError):
    """An upstream provider rate-limited us, after the HTTP client's own retries.

    The message names a call the model can make, not a wait: most agents have no
    way to wait a minute, and an error that asks for something the caller can't
    do gets ignored or abandoned. The server adds the tool's name as the error
    passes through it (``server._name_rate_limited_tools``). ``retry_after_s``
    stays in the message for callers that can wait.
    """

    def __init__(self, provider: str, retry_after_s: float | None = None) -> None:
        self.provider = provider
        self.retry_after_s = retry_after_s
        self.tool: str | None = None
        clears = (
            f" The limit should clear within about {retry_after_s:.0f}s." if retry_after_s else ""
        )
        super().__init__(
            f"{provider} rate-limited this request.{clears} Call {_SAME_TOOL} again with the"
            " same arguments, one call at a time. Repeating an identical earlier query is"
            " free: results are cached."
        )

    def for_tool(self, name: str) -> None:
        """Name the tool to call again, keeping anything added to the message since."""
        if self.tool is None:
            self.tool = name
            self.args = (str(self).replace(_SAME_TOOL, name, 1),)


#: Stands in for the tool's name until the server fills it in.
_SAME_TOOL = "the same tool"


class GeoNoResults(GeoError):
    """A query was well-formed but matched nothing.

    Distinct from an error: the model should widen or rephrase, not retry
    verbatim.
    """


class GeoUnavailable(GeoError):
    """An optional dependency or dataset needed for this tool is not installed."""

    def __init__(self, feature: str, extra: str) -> None:
        uvx, pip = install_commands(extra)
        super().__init__(
            f"{feature} requires the optional '{extra}' extra, which is not installed. "
            f"Install it with:  {uvx}   (or:  {pip})"
        )
