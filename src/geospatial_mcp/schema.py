"""Trim generated JSON Schemas before they reach the model.

Every tool's schema is re-sent on every turn, so schema size is a recurring
tax on the context window rather than a one-off cost. Pydantic generates
correct but verbose schemas; five things in them carry no information a model
can use:

1. **Auto-generated ``title`` keys.** Pydantic emits ``"title": "Distance M"``
   for a field already named ``distance_m``. Pure restatement.
2. **``additionalProperties: false``** on every nested definition. It comes from
   our own ``extra="forbid"`` config, which is worth having at runtime but says
   nothing useful about an output the model only reads.
3. **Verbose ``anyOf`` null unions.** ``{"anyOf": [{"type": "number"},
   {"type": "null"}]}`` collapses to ``{"type": ["number", "null"]}``, which is
   equivalent in JSON Schema and roughly half the length.
4. **Docstring indentation.** The SDK uses the raw docstring as the tool
   description, so every continuation line arrives with the eight spaces of
   source indentation in front of it. Unlike output schemas, descriptions reach
   the model in every client.
5. **The ``ResponseMeta`` definition**, repeated verbatim in all 22 output
   schemas. The server instructions describe ``meta`` once instead.

Measured on the 22 default tools, this cuts ``tools/list`` from about 78 KB to
40 KB. Field ``description`` text is never reworded -- that is the part the
model actually needs.
"""

from __future__ import annotations

import inspect
from typing import Any

__all__ = ["schema_size", "slim_schema", "slim_server_schemas"]

#: Keys safe to drop wholesale.
_DROP_KEYS = frozenset({"title", "additionalProperties"})

#: Definitions repeated verbatim in every tool's ``$defs``. The server
#: instructions explain them once, so each copy is reduced to a bare object.
_BOILERPLATE_DEFS = frozenset({"ResponseMeta"})


def slim_schema(node: Any) -> Any:
    """Recursively strip redundant keys from a JSON Schema."""
    if isinstance(node, list):
        return [slim_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        out[key] = slim_schema(value)

    # Collapse `anyOf: [{type: X}, {type: null}]` into `type: [X, null]`.
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        types = [
            branch.get("type")
            for branch in any_of
            if isinstance(branch, dict) and set(branch.keys()) <= {"type"}
        ]
        if len(types) == 2 and "null" in types and all(types):
            concrete = next(t for t in types if t != "null")
            del out["anyOf"]
            out["type"] = [concrete, "null"]
    return out


def schema_size(server: Any) -> int:
    """Serialized size in characters of a server's full tool listing."""
    import json

    return len(
        json.dumps(
            [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in _as_mcp_tools(server)
            ]
        )
    )


def _as_mcp_tools(server: Any) -> list[Any]:
    import asyncio

    return asyncio.run(server.list_tools())


def _collapse_boilerplate_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce definitions that appear in every tool's schema to a bare object.

    The ``$ref`` pointing at each one is kept, so the output schema still
    validates the same responses; it just stops spelling out the same six
    fields 22 times.
    """
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return schema
    for name in _BOILERPLATE_DEFS:
        if name in defs:
            defs[name] = {"type": "object"}
    return schema


def slim_server_schemas(server: Any) -> None:
    """Apply :func:`slim_schema` to every registered tool, in place."""
    for tool in server._tool_manager.list_tools():
        if getattr(tool, "description", None):
            tool.description = inspect.cleandoc(tool.description)
        if getattr(tool, "parameters", None):
            tool.parameters = slim_schema(tool.parameters)
        if getattr(tool, "output_schema", None):
            tool.output_schema = _collapse_boilerplate_defs(slim_schema(tool.output_schema))
