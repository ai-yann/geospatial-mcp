"""A rate-limit error names a call the model can make, not a wait.

Most agents can't wait a minute: they have no clock or sleep tool, only tool
calls. So the message names the tool to call again, filled in by the server as
the error passes through it, and keeps anything other code added on the way.
"""

from __future__ import annotations

import json

from geospatial_mcp.errors import GeoRateLimited
from geospatial_mcp.net.providers import openmeteo

from ..conftest import mcp_client


def _message_of(result) -> str:
    if result.structured_content:
        return json.dumps(result.structured_content)
    return "".join(getattr(block, "text", "") for block in (result.content or []))


def _rate_limited(*_: object, **__: object):
    raise GeoRateLimited("Open-Meteo")


class TestRateLimitMessage:
    async def test_the_model_is_told_which_call_to_make_again(self, monkeypatch):
        monkeypatch.setattr(openmeteo, "elevations", _rate_limited)
        async with mcp_client() as client:
            result = await client.call_tool("terrain_elevation", {"location": "36.6205,-4.4996"})
        message = _message_of(result)
        assert result.is_error
        assert "Open-Meteo rate-limited this request" in message
        assert "Call terrain_elevation again with the same arguments, one call at a time" in message
        assert "the same tool" not in message
        assert "minute" not in message

    def test_a_wait_the_provider_gave_is_kept(self):
        exc = GeoRateLimited("Nominatim", retry_after_s=30)
        assert "clear within about 30s" in str(exc)

    def test_naming_the_tool_keeps_text_added_since(self):
        exc = GeoRateLimited("Nominatim")
        exc.args = (
            f"{exc} If 'gcpvj' is a geohash rather than a place name, write 'geohash:gcpvj'.",
        )
        exc.for_tool("place_geocode")
        assert "Call place_geocode again" in str(exc)
        assert "write 'geohash:gcpvj'" in str(exc)
        exc.for_tool("other_tool")
        assert "other_tool" not in str(exc)
