# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `GEO_TRACE_RESULTS=1` adds a `preview` to each line of the `GEO_TRACE_FILE`
  call log: the result's headline values (a cell radius, an elevation, the
  place a name was matched to), its first warning and note, and whether it came
  from the cache. Warnings and notes keep up to 200 characters, other text up
  to 100, and anything longer is cut between words and ends in an ellipsis.
  The whole preview is capped at 600 characters and never includes geometry.
- `evals/harness.py` warns on stderr when the path of its work root
  (`--work-root` or `GEO_EVALS_WORK_ROOT`) holds a word that could tell the
  model what is being tested, such as `geospatial`, `mcp`, `eval`, `tool` or a
  tool-name domain like `rf`, and records the words in `summary.json` as
  `work_root_hint_words`. Claude Code shows the model its working directory. On
  `telecom-cell-area-sum-007` with Sonnet 5.5 in October 2026, runs under
  `/Users/Shared/geospatial-evals` called `rf_link_budget` and passed in 6 of 11
  runs, and runs under neutral folders in 0 of 20. That is one case and one
  model, so how far the effect reaches is not known. `evals/README.md` now
  recommends a neutral work root such as `/Users/Shared/agent-runs`. The
  default, `~/.cache/geospatial-evals`, carries the hint but is unchanged, so
  that new runs compare with the Claude Code runs recorded under it in
  September 2026, whose tool use the hint may have inflated.

### Changed

- The default `tools/list` is about 16% smaller (47.8k to 40.0k characters).
  Tool descriptions no longer carry docstring indentation, and the `meta`
  definition repeated in every output schema is reduced to a bare object; the
  server instructions describe `meta` once.
- `rf_link_budget` now says that `cell_area_km2` is the hexagonal area one site
  serves in a tiled plan (2.598·r²), not the area of one isolated circular cell
  (π·r²). Summing it for non-overlapping sites undercounted their coverage by
  about 17%, and nothing in the schema said so.
- Distances under 1 m are refused as probably being degrees, in
  `geom_transform` buffers and the `rf_towers`, `place_search` and
  `stats_cluster` (DBSCAN) radii. A model that converted 7.5 km to 0.0674 and
  sent it as metres got a 7 cm buffer, or an empty search reported as an
  absence of data. The error now says what happened and how to fix it.
- Open-Meteo elevation responses are cached for a year. They used to expire
  after ten minutes, the lifetime meant for forecasts from the same host, so a
  terrain profile or line-of-sight check could not be fetched in advance.
- The release workflow no longer uploads to PyPI. The project owns no package
  there and installs from this repository, so a release only builds the wheel
  and sdist. The publish job is kept, switched off, with the steps for turning
  it back on.

### Fixed

- Error messages reach the model with every `mcp` 2.x. From `mcp` 2.1 the SDK
  tells the model only `Error executing tool <name>` when a tool raises
  anything other than a `ToolError`, so on a fresh install every input error,
  including the one that explains a distance looks like degrees, lost its text.
  `GeoError` now subclasses `ToolError`. `uv.lock` still pins 2.0.0, so a new CI
  job runs the tests against the newest 2.x as well.
- The install commands in error messages about a missing extra fetch the
  server from this repository. They named a PyPI package, but the server is
  not on PyPI, and the PyPI package named `geospatial-mcp` is an unrelated
  project. `server.json` no longer lists a PyPI package for the same reason.
- `coord_transform_crs` reports a change of projection or axis order with no
  datum transformation as exact on every PROJ, and so does a call with the
  same CRS on both sides. It used to say the accuracy was unknown in two
  cases. PROJ 9.8, which pyproj 3.8 bundles and a fresh install resolves,
  publishes no accuracy for operations such as EPSG:4326 to EPSG:3857. And on
  PROJ 9.5 and 9.8 alike, an axis-order change alone (EPSG:4326 to OGC:CRS84)
  or a CRS to itself (EPSG:3857 to EPSG:3857) leaves no record of the operation
  used for the point, so the tool had no steps to inspect. An operation with
  any datum transformation step, a ballpark offset included, is still never
  counted as exact.
- A place name spelt only from the geohash alphabet is geocoded, not decoded
  as a geohash. Any `location` of four or more such characters used to be
  read as a geohash with no note, so "Denver" became 17.88, -57.86 in the
  Atlantic, "Bergen" and "Berne" landed in Alaska, and "Sydney", "Perth",
  "Sweden", "SW19" and the postcode "90210" were misplaced the same way. A
  bare token is now decoded at once only when it is written the way geohashes
  are, in lowercase with a digit among the letters, such as `u09tunquc`. Any
  other token that could be a geohash is looked up as a place first, and
  `meta.notes` gives the geohash reading and how to ask for it. If no place
  has the name, the token is read as a geohash, with a note saying so:
  Nominatim has no place called `gcpvj`, so it is still a cell in London. To
  skip the lookup, mark the geohash: `geohash:gcpvj`. `coord_describe`, which
  makes no network call, and the tools that take only coordinates treat such
  a word as a place name too, and their messages give the marked form; in
  those tools a geohash in capitals, such as `U09TUNQUC`, needs the mark as
  well. A postcode district in lowercase, such as `sw19`, still reads as a
  geohash under this rule, so any unmarked 4-character geohash now comes
  with a note giving its cell, about 39 km wide, and saying to add the town
  or country if it is a postcode.

## [0.1.0]

First release.

### Added

**Transport and packaging**

- An MCP server speaking stdio by default, so it runs under Claude Code, Claude
  Desktop, OpenAI Codex CLI, Cursor, VS Code, Zed, Windsurf and Gemini CLI.
  Streamable HTTP is available with `--transport http` for self-hosting, and
  binds `127.0.0.1` unless the platform sets `PORT`.
- Installable with `uvx` from this repository as `mcp-geospatial`, with no
  system packages: binary wheels on Linux, macOS and Windows across x86-64 and
  ARM.
- Toolsets gated by `GEO_TOOLSETS` / `--toolsets`, with `default` (22 tools),
  `all`, and an `offline` preset of the 13 tools that make no network call.

**Coordinates — `core`**

- `coord_convert`, `coord_describe`, `coord_transform_crs`. One polymorphic
  `location` parameter accepts decimal degrees, DMS, a USNG/MGRS grid reference,
  geohash, H3, Plus Codes, or a place name to geocode. CRS transforms name the
  operation PROJ used for each point and its accuracy, the grid file a more
  accurate one would need, and whether the datum shift was ignored entirely.

**Geometry — `core`**

- `geom_measure`, `geom_transform`, `geom_overlay`, `geom_relate`. Distance,
  bearing and area on the WGS84 ellipsoid; buffers projected to an azimuthal
  equidistant plane so a radius in metres is that distance on the ground at any
  latitude.

**Places and routing — `core`**

- `place_geocode`, `place_reverse`, `place_search`, `route_directions`,
  `route_isochrone`, `route_matrix`. Keyless throughout: Nominatim and Photon
  for geocoding, Overpass for category search, Valhalla for routing, isochrones
  and origin-destination matrices.

**Terrain, environment and formats — `core`**

- `terrain_elevation`, `terrain_profile`, `env_weather`, `sun_moon`,
  `time_at_location`, `data_convert`, `geo_capabilities`. Profiles sample a true
  geodesic and batch every elevation into one request; sun position and timezone
  lookup run entirely offline.

**Radio links — `rf`**

- `rf_link_budget` and `rf_towers`. Free-space, two-ray, Okumura-Hata and
  COST 231-Hata path loss, solved for the distance at which the budget closes.
  Models report when parameters fall outside their published validity range,
  because an empirical model used out of range still returns a confident number.

**Analysis — `analysis`, opt-in via the `stats` extra**

- `stats_cluster`, `coord_grid_cells`. Clustering uses the haversine metric, so
  a radius in metres is a ground distance rather than a degree-space ellipse.

**Behaviour**

- Tools raise on failure instead of returning an error payload, so MCP marks
  the response `isError` and a model does not read failure as success.
- Every input error names what was received, why it is wrong, and a valid
  example.
- DEM gaps are reported as `null`, never substituted with zero.
- Grid references truncate rather than round, so a coarser reference always
  names the square the position is actually in.
- A place name matching several similarly prominent places far apart is refused
  with the candidates listed, rather than silently resolved to the first. When a
  name is used, `meta.notes` says which place it was taken to mean.
- Responses carry the attribution their data licence requires in
  `meta.attribution`, and accuracy caveats in `meta.notes`.
- A shared HTTP layer with per-host rate limiting, a disk cache, bounded
  retries and mirror failover, to stay inside the public services' usage
  policies.

**Project**

- 293 tests, including golden vectors cross-checked against independent
  implementations, and MCP contract tests covering tool naming, descriptions,
  annotations and schema budget.
- A 31-case tool-selection eval library under `evals/`.
- CI across Python 3.11–3.13 on Linux, plus macOS and Windows.

[Unreleased]: https://github.com/ystoneman/geospatial-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ystoneman/geospatial-mcp/releases/tag/v0.1.0
