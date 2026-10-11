# AGENTS.md

Working notes for coding agents and humans. Commands first.

## Commands

```bash
uv sync --all-extras       # set up
make check                 # lint + types + tests. Run before every commit.
make test                  # pytest, offline only (default)
make lint                  # ruff check + format --check
make fix                   # ruff --fix + format
make types                 # mypy
make evals                 # tool-selection evals (offline cases)
make evals-network         # every eval case, against the live APIs
make run                   # start the server on stdio
make tools                 # print the tool catalogue as JSON
```

A single test: `uv run pytest tests/geo/test_rf.py::TestFreeSpace -v`

## Layout

```
src/geospatial_mcp/
  _sdk.py        THE ONLY module that imports from `mcp`. Isolates SDK churn.
  server.py      build_server(toolsets) -> MCPServer. Wiring only, no logic.
  cli.py         argparse entry point; the only place that prints to stdout.
  config.py      Settings.from_env(). stdlib only.
  errors.py      GeoInputError and friends. Tools RAISE these.
  models.py      ResponseMeta and shared response models.
  schema.py      Trims generated JSON Schemas before they reach the model.
  geo/           PURE computation. No network, no filesystem, no clock.
  net/           One httpx client, one disk cache, one rate limiter.
    providers/   One module per upstream service. Knows nothing about MCP.
  tools/         One module per toolset. Each exposes register(mcp) -> None.
tests/           Mirrors src/. Offline by default.
evals/           Tool-selection prompt library and runner.
```

## Non-negotiables

1. **Never write to stdout.** The stdio transport reserves it for JSON-RPC.
   Logging goes to stderr via `server.configure_logging`. `cli.py` is the one
   exception, and only before a transport starts.
   Guarded by `tests/mcp/test_stdio.py`.

2. **Tools raise, they never return an error payload.** Returning
   `{"status": "error"}` makes MCP mark the call successful, and the model then
   works from invented data. Raise `GeoInputError` for anything the model could
   fix by retrying.

3. **Every error message names the input, the problem, and a valid example.**
   Use `GeoInputError.with_example(...)`. Enforced by
   `tests/mcp/test_error_quality.py`.

4. **Only `_sdk.py` imports from `mcp`.** Everything else imports from `_sdk`.

5. **`geo/` stays pure.** If it needs the network, a file, or the time, it
   belongs in `net/` or `tools/`, not `geo/`.

6. **Geometry parameters accept a JSON object *or* a string.** Use
   `GeometryInput` and `as_geometry_text`. The SDK pre-parses JSON-looking
   string arguments into dicts, so a `str`-typed parameter rejects exactly what
   the client was told to send.

7. **No degree-space geometry.** Distances via `geo.geodesy`, buffers via
   `geo.geometry.geodesic_buffer`. `111.32 km/degree` is a bug.

8. **DEM gaps are `None`, never `0.0`.** Substituting sea level turns a missing
   sample into a confident wrong one, and every statistic downstream inherits it.

9. **Grid references truncate, they never round.** A reference names the square
   a position falls in; rounding would hand back a neighbouring square.

10. **No unverifiable claims about other projects.** No "the only", "the first",
    "no other server does this". *"As far as we can tell"* is not a hedge — it is
    the phrasing that lets an unchecked claim ship. Say what this package does
    and link to a live index so the reader can compare; a superlative goes stale
    silently and is wrong in public. Enforced by
    `tests/mcp/test_doc_claims.py`.

## Adding a tool

1. Put the computation in `geo/` (pure) or a provider in `net/providers/`.
2. Add the tool function to the right module in `tools/`, inside `register()`.
3. Annotate every parameter with `Annotated[T, Field(description=...)]`.
4. Return a pydantic model with a `meta: ResponseMeta` field. Use
   `_shared.meta(...)` so attribution is filled in.
5. Write the docstring as prompt surface: first line a standalone sentence,
   then what it is for, then **when to use a neighbouring tool instead**.
6. Annotate with `compute()` (no network) or `network()`.
7. Add unit tests in `tests/geo/` and at least one eval case in
   `evals/prompts.yaml`.
8. `make check`.

The contract tests in `tests/mcp/test_contract.py` will fail if a tool name
breaks convention, a parameter is undocumented, a near-neighbour is not
cross-referenced, or the catalogue exceeds its context budget.

## Conventions

- Tool names are `<domain>_<intent>`. No `get_` or `calculate_` prefixes.
- Locations are one polymorphic `location: str`, never a
  `(grid_reference, latitude, longitude)` triple.
- British spelling in prose, US spelling in identifiers (`normalize`, `meters`
  only where an external API uses it).
- Comments explain *why*, especially where the obvious approach is wrong.

## Gotchas

- `mcp` 2.x renamed `FastMCP` to `MCPServer`; there is no `fastmcp` module.
- From `mcp` 2.1 only a `ToolError`'s message reaches the model; any other
  exception from a tool arrives as `Error executing tool <name>`. `GeoError`
  subclasses `ToolError` for this reason, so raise a `Geo*Error`, never a bare
  `ValueError`, for anything the model should read. `uv.lock` pins 2.0, which
  shows every message, so only the `newest-sdk` CI job would notice.
- `ToolAnnotations` fields are snake_case in Python, camelCase on the wire.
- Response models are defined inside `register()`, so the tool modules must
  **not** use `from __future__ import annotations` — PEP 563 string annotations
  cannot be resolved from a function-local scope.
- Overpass mirrors differ in coverage. `overpass.osm.ch` serves Swiss data only
  and answers a query for Paris with HTTP 200 and zero results. Only add
  global mirrors to `GLOBAL_MIRRORS`.
