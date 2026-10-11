---
name: add-tool
description: >
  Add a new tool to the mcp-geospatial server. Use when asked to add a
  geospatial capability, wrap a new data provider, or expose an existing
  geo/ function as an MCP tool.
---

# Adding a tool to mcp-geospatial

Follow this order. Skipping straight to `tools/` produces a tool that works but
cannot be unit-tested and duplicates logic.

## 1. Decide where the logic lives

| If it... | Put it in |
|---|---|
| Is pure computation (maths, formats, projections) | `src/geospatial_mcp/geo/<module>.py` |
| Calls an external service | `src/geospatial_mcp/net/providers/<service>.py` |

`geo/` must stay free of network, filesystem and clock access. That purity is
what makes the golden-vector tests possible.

## 2. If it is a new provider

- Add a rate limit to `net/ratelimit.py::HOST_LIMITS` matching the service's
  published policy.
- Add a cache TTL to `net/cache.py::TTL_BY_HOST` reflecting how fast the data
  actually changes.
- Add its attribution line to `attribution.py::SOURCES`.
- Add a base-URL setting to `config.py` so users can self-host.
- Provide a keyless path, or a keyless fallback provider.

## 3. Register the tool

In the right module under `src/geospatial_mcp/tools/`, inside `register()`:

```python
class ThingResult(GeoModel):
    value: float
    meta: ResponseMeta

@mcp.tool(annotations=compute())          # or network()
def domain_intent(
    location: Annotated[str, Field(description="Position, as coordinates or a place name.")],
) -> ThingResult:
    """One-line summary ending in a full stop.

    What this is for, in terms of real tasks and industries.

    When to use a neighbouring tool instead -- name it explicitly.
    """
    loc = resolve(location)
    lat, lon = loc.as_tuple()
    ...
    return ThingResult(value=..., meta=meta("provider_key", cached=cached))
```

Rules the contract tests enforce:

- Name is `<domain>_<intent>`. No `get_` or `calculate_` prefix.
- Every parameter has `Annotated[T, Field(description=...)]`.
- The description's first line is a standalone sentence, 20–200 characters.
- Near-neighbour tools cross-reference each other.
- The whole catalogue stays under the schema character budget.

## 4. Watch for these

- **Do not add `from __future__ import annotations`** to a tools module.
  Response models are defined inside `register()`, and PEP 563 string
  annotations cannot be resolved from a function-local scope — the SDK raises
  `InvalidSignature`.
- **Geometry parameters** use `GeometryInput` + `as_geometry_text()`, never
  bare `str`. The SDK pre-parses JSON-looking string arguments into dicts.
- **Raise, never return an error.** `GeoInputError.with_example(...)`.
- **Never write to stdout.**

## 5. Test it

1. Unit tests for the pure logic in `tests/geo/`.
2. At least one case in `evals/prompts.yaml`, with `expect_tools`,
   `forbid_tools` naming the tools it could be confused with, and a `validate`
   block.
3. `make check && make evals`.

## 6. Record it

- `CHANGELOG.md` under `[Unreleased]`.
- The tool table in `README.md`.
- `THIRD_PARTY_NOTICES.md` if you added a dependency.
