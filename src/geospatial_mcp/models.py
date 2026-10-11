"""Shared response models.

Tools return pydantic models, not dicts. The return annotation *is* the output
schema the SDK publishes in ``tools/list`` and validates against before
transmission, so a typed response is simultaneously documentation, validation
and a contract that tests can snapshot.

Every response embeds :class:`ResponseMeta`, which tells the model where the
data came from, whether it was cached, whether the call touched the network,
and any accuracy caveat it should mention rather than silently swallow.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BBox", "GeoModel", "LatLon", "ResponseMeta", "Units"]

Units = Literal["metric", "imperial", "nautical"]


class GeoModel(BaseModel):
    """Base for every response model.

    ``extra="forbid"`` catches typo'd field names at construction time instead
    of silently dropping data from a response the model then relies on.
    """

    model_config = ConfigDict(extra="forbid")


class ResponseMeta(GeoModel):
    """Provenance and caveats. See the server instructions for how to use it.

    Field descriptions here are deliberately terse: this model is embedded in
    the output schema of every single tool, so each character is paid for once
    per tool on every turn. The full explanation lives once, in the server
    instructions, rather than once per tool in the schema.
    """

    sources: list[str] = Field(default_factory=list, description="Data providers used.")
    attribution: list[str] = Field(
        default_factory=list, description="Attribution lines to show users. Required by licence."
    )
    offline: bool = Field(default=True, description="Computed with no network call.")
    cached: bool = Field(default=False, description="Served from local cache.")
    truncated: bool = Field(default=False, description="Results were capped; narrow the query.")
    notes: list[str] = Field(
        default_factory=list, description="Accuracy caveats worth repeating to the user."
    )


class LatLon(GeoModel):
    """A WGS84 geographic position."""

    latitude: Annotated[float, Field(ge=-90, le=90, description="Degrees north of the equator.")]
    longitude: Annotated[
        float, Field(ge=-180, le=180, description="Degrees east of the prime meridian.")
    ]


class BBox(GeoModel):
    """A WGS84 bounding box. Ordered as in GeoJSON: west, south, east, north."""

    west: Annotated[float, Field(ge=-180, le=180)]
    south: Annotated[float, Field(ge=-90, le=90)]
    east: Annotated[float, Field(ge=-180, le=180)]
    north: Annotated[float, Field(ge=-90, le=90)]
