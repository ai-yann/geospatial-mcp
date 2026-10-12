# mcp-geospatial

<!-- mcp-name: io.github.ystoneman/geospatial -->

An MCP server for geospatial work: coordinates, geocoding, routing, terrain,
weather and radio links. 22 tools, no API key required for any of them.

Built for logistics, telecom, agriculture, insurance, real estate, utilities,
emergency response, surveying and mapping — anywhere a question starts with
*where*.

The package and command are `mcp-geospatial`. The server is not on PyPI under
either name: install it from this repository, as below. The PyPI package
named `geospatial-mcp` is an unrelated project; installing it will not give
you this server.

```
"Where can I live within a 30-minute drive of work?"          route_isochrone
"Where's the nearest depot by road to each of today's jobs?"  route_matrix
"How long to drive from the depot to the customer?"           route_directions
"How steep is this section of the trail?"                     terrain_profile
"How far will a 1800 MHz cell reach over suburban ground?"    rf_link_budget
"What's the area of this field in hectares?"                  geom_measure
"Convert this grid reference the survey crew gave me"         coord_convert
```

## Install

Nothing to clone by hand. Every client below runs the server with `uvx`,
straight from this repository. To install it into an environment of your own
instead, use `pip install git+https://github.com/ystoneman/geospatial-mcp`.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add geospatial -- uvx --from git+https://github.com/ystoneman/geospatial-mcp mcp-geospatial
```
</details>

<details>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

```json
{
  "mcpServers": {
    "geospatial": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ystoneman/geospatial-mcp", "mcp-geospatial"]
    }
  }
}
```
</details>

<details>
<summary><b>OpenAI Codex CLI</b> — <code>~/.codex/config.toml</code></summary>

```toml
[mcp_servers.geospatial]
command = "uvx"
args = ["--from", "git+https://github.com/ystoneman/geospatial-mcp", "mcp-geospatial"]
```
</details>

<details>
<summary><b>Cursor</b> — <code>.cursor/mcp.json</code></summary>

```json
{
  "mcpServers": {
    "geospatial": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ystoneman/geospatial-mcp", "mcp-geospatial"]
    }
  }
}
```
</details>

<details>
<summary><b>VS Code</b> — <code>.vscode/mcp.json</code> (note: the key is <code>servers</code>, not <code>mcpServers</code>)</summary>

```json
{
  "servers": {
    "geospatial": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ystoneman/geospatial-mcp", "mcp-geospatial"]
    }
  }
}
```
</details>

<details>
<summary><b>Zed</b> — <code>settings.json</code> (the key is <code>context_servers</code>)</summary>

```json
{
  "context_servers": {
    "geospatial": {
      "command": { "path": "uvx", "args": ["--from", "git+https://github.com/ystoneman/geospatial-mcp", "mcp-geospatial"] }
    }
  }
}
```
</details>

<details>
<summary><b>Docker / self-hosted HTTP</b></summary>

```bash
docker build -t mcp-geospatial https://github.com/ystoneman/geospatial-mcp.git
docker run -p 8000:8000 mcp-geospatial --transport http
```

The HTTP transport binds `127.0.0.1` unless `PORT` is set by the platform. Put
it behind a reverse proxy with authentication before exposing it publicly.
</details>

## Tools

Every tool taking a `location` accepts **any** of these, interchangeably:

```
48.8584, 2.2945          decimal degrees (canonical, latitude first)
48°51'30"N 2°17'40"E     degrees / minutes / seconds
31UDQ4825211954          USNG / MGRS grid reference
u09tunquc                geohash
geohash:u09tunquc        geohash, marked so it is never taken for a place name
891fb46741bffff          H3 cell
8FW4V75V+9R6             Plus Code
Eiffel Tower, Paris      a place name, geocoded for you
```

### `core` — 20 tools, on by default

| Tool | What it answers | Offline |
|---|---|:--:|
| `coord_convert` | This position in every notation at once | ● |
| `coord_describe` | Is this a valid coordinate, and how precise? | ● |
| `coord_transform_crs` | Between EPSG systems, with an accuracy figure | ● |
| `geom_measure` | Distance, bearing, area, perimeter, centroid | ● |
| `geom_transform` | Buffer, simplify, hull, centroid, repair | ● |
| `geom_overlay` | Intersection, union, difference | ● |
| `geom_relate` | Point-in-polygon, overlap, nearest distance | ● |
| `place_geocode` | Address or place name → coordinates | ○ |
| `place_reverse` | Coordinates → address | ○ |
| `place_search` | Fuel, hospitals, chargers, warehouses nearby | ○ |
| `route_directions` | Driving, cycling, walking, HGV directions | ○ |
| `route_isochrone` | Everywhere reachable in N minutes | ○ |
| `route_matrix` | Travel times between many origins and destinations | ○ |
| `terrain_elevation` | Height above sea level | ○ |
| `terrain_profile` | Elevation along a path, ascent and descent | ○ |
| `env_weather` | Current conditions and forecast | ○ |
| `sun_moon` | Sunrise, twilight, golden hour, shadow length | ● |
| `time_at_location` | Time zone, local time, next DST change | ● |
| `data_convert` | GeoJSON ⇄ WKT ⇄ KML ⇄ GPX ⇄ CSV ⇄ polyline | ● |
| `geo_capabilities` | What is enabled, and how to enable more | ● |

### `rf` — 2 tools, on by default

| Tool | What it answers | Offline |
|---|---|:--:|
| `rf_link_budget` | Path-loss budget and cell radius | ● |
| `rf_towers` | Known cell towers nearby | ○ |

### `analysis` — 2 tools, opt-in (the `stats` extra)

| Tool | What it answers | Offline |
|---|---|:--:|
| `stats_cluster` | Where do these points concentrate? | ● |
| `coord_grid_cells` | Bin points into H3 or geohash cells | ● |

● runs with no network at all. ○ calls a free public API.

Link budgets are an everyday tool for mobile operators, wireless ISPs,
utilities running SCADA telemetry and IoT fleets: how far a radio reaches for a
given power, frequency and environment.

This is one server among many. If you are working out which to use, Sparkgeo's
[geo-mcp-servers](https://github.com/sparkgeo/geo-mcp-servers) is a curated,
machine-checked index of the geospatial MCP field, kept current and marked for
liveness — a better place to compare coverage than any claim this README could
make about itself. [docs/REFERENCES.md](docs/REFERENCES.md) lists the servers,
standards and research this project leans on or points at.

### Selecting toolsets

The examples call the command directly. If it is not installed, prefix it
with `uvx --from git+https://github.com/ystoneman/geospatial-mcp`, as in the
client configs above. `all` also needs the `stats` extra:
`uvx --from 'mcp-geospatial[stats] @ git+https://github.com/ystoneman/geospatial-mcp' mcp-geospatial --toolsets all`.

```bash
mcp-geospatial --toolsets core       # 20 tools, drops the RF set
mcp-geospatial --toolsets all        # adds the analysis toolset (needs [stats])
GEO_TOOLSETS=offline mcp-geospatial  # 13 tools that never touch the network
mcp-geospatial --list-tools          # print the catalogue and exit
```

`offline` is for field use with no signal. It keeps only tools that make no
network call under *any* input, so it excludes `geom_measure` and the place
tools — those accept a place name, which means they may geocode.

| Toolset | Tools | Needs |
|---|--:|---|
| `core` | 20 | nothing |
| `rf` | 2 | nothing |
| `analysis` | 2 | the `stats` extra (see above) |

## Configuration

No API key is needed for anything. Every keyed provider has a keyless default.

| Variable | Purpose |
|---|---|
| `GEO_TOOLSETS` | `default`, `all`, `offline`, or a comma-separated list |
| `GEO_CACHE_DIR` | Response cache location (default `~/.cache/mcp-geospatial`) |
| `GEO_CACHE` | `0` disables the disk cache |
| `GEO_USER_AGENT` | Override the User-Agent sent to providers |
| `GEO_TRACE_FILE` | Append one JSON line per tool call to this file (off by default; records arguments, so it holds locations) |
| `GEO_TRACE_RESULTS` | `1` adds a short `preview` of each result to those lines: headline values, the first warning and note, and whether it was cached. Never geometry |
| `NOMINATIM_URL`, `PHOTON_URL`, `OVERPASS_URL`, `VALHALLA_URL`, `OPEN_METEO_BASE_URL` | Point at self-hosted instances |
| `OPENROUTESERVICE_API_KEY` | Optional routing upgrade over the keyless default |
| `OPENCELLID_API_KEY` | Optional; larger cell-tower database |
| `OPEN_METEO_API_KEY` | Required for **commercial** weather use (see below) |

### Data sources and licensing

Results carry the attribution their licence requires in `meta.attribution` —
pass it through when you display them. Two obligations worth knowing before you
build on this: OpenStreetMap-derived data (geocoding, places, routing) is
**ODbL 1.0** and requires attribution, and **Open-Meteo's free tier is
non-commercial**, so commercial deployments need `OPEN_METEO_API_KEY` or a
self-hosted instance.

The public instances are free and shared. This server caches aggressively and
rate-limits per host to stay inside their usage policies; for heavy use,
self-host and set the corresponding `*_URL`. See
[Sources and credits](#sources-and-credits) for the complete list.

## What it gets right that is easy to get wrong

- **Distances and areas are geodesic.** Karney's algorithm on the WGS84
  ellipsoid, accurate to millimetres at any distance. Not `111.32 km/degree`,
  which is 0.25% out at 60° latitude and worse further north.
- **Buffers are circles on the ground.** Buffering in degrees produces an
  ellipse stretched by 1/cos(latitude) — 2× wrong at 60°, 5× at 78°. Every
  buffer here is projected to an azimuthal equidistant plane first.
- **Grid references truncate, they never round.** A reference names the square a
  position falls in, so dropping digits must move the point to the coarser
  square's corner. Rounding would hand back a neighbouring square — a kilometre
  away at four digits, and confidently wrong.
- **DEM gaps stay gaps.** A missing elevation is reported as `null`, never
  substituted with zero. Substituting sea level flattens a ridge into an
  entirely plausible plain, and every statistic downstream inherits it.
- **Place names are never silently guessed.** "Springfield" matches cities of
  near-equal prominence 1,500 km apart, so a tool asked about it lists them and
  asks, rather than quietly answering for Illinois. When a name is clear enough
  to use, `meta.notes` says exactly which place it was taken to mean, and names
  any others that share it.
- **A place name is not mistaken for a code.** "Bergen", "Denver" and "Sydney"
  are spelt entirely from the geohash alphabet, and decoding them lands in
  Alaska, the Atlantic and Turkey. A bare token is decoded as a geohash at
  once only when it is written like one, in lowercase with a digit among the
  letters. Anything else that could be a geohash is geocoded first, with a
  note in `meta.notes` giving the geohash reading, and is read as a geohash
  only if no place has the name. Write `geohash:gcpvj` to skip the lookup. A
  4-character geohash such as `sw19` could be a postcode district, so it is
  decoded with a note saying so.
- **Errors are errors.** Failures raise, so the MCP response is marked
  `isError`. A tool that returns `{"status": "error"}` is reported to the model
  as a success, and it will proceed on invented data.
- **Empirical models report their limits.** COST231-Hata outside 1500–2000 MHz
  still returns a number; `meta.notes` tells you it is out of range.
- **CRS transforms admit their accuracy.** Every transform names the operation
  PROJ used for that point and its published accuracy. When a better operation
  needs a grid file that is not installed, the response names the file; when no
  published transformation exists at all, it says the datum shift was ignored.

## Development

```bash
git clone https://github.com/ystoneman/geospatial-mcp && cd geospatial-mcp
uv sync --all-extras
make check          # lint, types, tests — exactly what CI runs
make evals          # tool-selection eval suite
make evals-network  # the same, plus the cases that call live APIs
```

See [AGENTS.md](AGENTS.md) for the layout and conventions, and
[CONTRIBUTING.md](CONTRIBUTING.md) to add a tool.

## Sources and credits

Almost none of the geospatial computation here is ours. This package is an
integration layer: it chooses which established libraries, published algorithms
and public data services to expose to a language model, wires them together, and
tries hard to get the units, datums and error handling right. The interesting
work was done by the projects below, and this section exists so you can see
exactly which ones you are depending on.

To put a number on it — of the Python that ships when you install this package,
**1.0% is ours and 99.0% is dependencies** (6,912 lines against 654,791), inside
a 150 MB installation of which 109 MB is compiled native libraries and 9 MB is
the EPSG registry. Counted by installing the package with no extras into an
empty environment and measuring `site-packages`.

### Runtime dependencies

Everything below is permissively licensed. Licences were read from each
package's own `LICENSE` file, not from its classifiers — the two disagree more
often than you would expect.

| Package | Version | Licence | What it provides here |
|---|---|---|---|
| [`mcp`](https://github.com/modelcontextprotocol/python-sdk) | 2.0.0 | MIT | The Model Context Protocol SDK: tool registration, transports, schemas |
| [`pydantic`](https://docs.pydantic.dev) | 2.12.5 | MIT | Tool input and output schemas, response validation |
| [`numpy`](https://numpy.org) | 2.4.2 | BSD-3-Clause and others | Terrain arrays, profile statistics, hillshade kernels |
| [`shapely`](https://shapely.readthedocs.io) | 2.1.2 | BSD-3-Clause | All polygon geometry — buffer, union, hulls, predicates |
| [`pyproj`](https://pyproj4.github.io/pyproj) | 3.7.2 | MIT | CRS transforms and every geodesic distance, bearing and area |
| [`pygeodesy`](https://github.com/mrJean1/PyGeodesy) | 26.8.18 | MIT | Rhumb lines, great-circle intersections, cross-track distance, USNG/MGRS grid references |
| [`h3`](https://h3geo.org) | 4.5.0 | Apache-2.0 | Uber's hexagonal hierarchical spatial index |
| [`geohashr`](https://github.com/hrbrmstr/geohashr) | 1.6.0 | BSD-3-Clause | Geohash encoding and decoding |
| [`pluscodes`](https://github.com/google/open-location-code) | 2022.1.3 | Apache-2.0 | Open Location Code (Plus Codes) |
| [`mercantile`](https://github.com/mapbox/mercantile) | 1.2.1 | BSD-3-Clause | XYZ tile and quadkey arithmetic |
| [`astral`](https://github.com/sffjunkie/astral) | 3.2 | Apache-2.0 | Sun and moon position, twilight, golden hour |
| [`tzfpy`](https://github.com/ringsaturn/tzfpy) | 1.3.3 | MIT | Offline timezone lookup from coordinates |
| [`geojson-pydantic`](https://github.com/developmentseed/geojson-pydantic) | 2.1.1 | MIT | Typed GeoJSON models |
| [`polyline`](https://github.com/frederickjansen/polyline) | 2.0.4 | MIT | Google encoded polyline format |
| [`pyshp`](https://github.com/GeospatialPython/pyshp) | 3.1.6 | MIT | Shapefile reading and writing without GDAL |

Optional extras: [`pillow`](https://python-pillow.org) (MIT-CMU),
[`sgp4`](https://github.com/brandon-rhodes/python-sgp4) and
[`skyfield`](https://rhodesmill.org/skyfield) (MIT),
[`scikit-learn`](https://scikit-learn.org), [`esda`](https://pysal.org/esda) and
[`libpysal`](https://pysal.org/libpysal) (BSD-3-Clause),
[`fast-tsp`](https://github.com/nickmelnikov82/fast-tsp) (MIT),
[`staticmap`](https://github.com/komoot/staticmap) (Apache-2.0),
[`rasterio`](https://rasterio.readthedocs.io) and
[`rio-tiler`](https://cogeotiff.github.io/rio-tiler) (BSD),
[`pystac-client`](https://pystac-client.readthedocs.io) (Apache-2.0),
[`morecantile`](https://developmentseed.org/morecantile) (MIT).

### Native libraries bundled inside those wheels

| Library | Licence | Reached through |
|---|---|---|
| [GEOS](https://libgeos.org) | **LGPL-2.1-or-later** | `shapely` — used unmodified and dynamically linked; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for how the licence is satisfied |
| [PROJ](https://proj.org) | MIT | `pyproj`, with the EPSG registry |

GEOS is the one copyleft component in the tree. It is unavoidable — it sits
under Shapely, GeoPandas, OGR, PostGIS and DuckDB-spatial alike, and there is no
permissive replacement with equivalent overlay correctness.

### Live data services

None require an API key. Results carry the attribution their licence demands in
`meta.attribution`; pass it through when you display them.

| Service | Provides | Data licence |
|---|---|---|
| [Nominatim](https://nominatim.org) / [Photon](https://photon.komoot.io) | Geocoding, reverse geocoding | [OpenStreetMap](https://www.openstreetmap.org/copyright), ODbL 1.0 |
| [Overpass API](https://overpass-api.de) | Points of interest by category | OpenStreetMap, ODbL 1.0 |
| [Valhalla](https://valhalla.github.io/valhalla) via [FOSSGIS](https://www.fossgis.de) | Routing, isochrones, matrices | OpenStreetMap, ODbL 1.0 |
| [Open-Meteo](https://open-meteo.com) | Weather, elevation, air quality | CC-BY 4.0 — **free tier is non-commercial** |
| Copernicus GLO-90 DEM | Elevation, via Open-Meteo | © DLR e.V. 2010–2014, © Airbus Defence and Space GmbH 2014–2018 |
| [BeaconDB](https://beacondb.net) | Cell towers (keyless default) | Public domain (CC0) |
| [OpenCelliD](https://opencellid.org) | Cell towers (optional, keyed) | CC-BY-SA 4.0 — share-alike on derived databases |

### Algorithms, and where they come from

Every computation in this package traces to a library, a published formula or a
public service. None of it was invented here, and the literature is cited so you
can check the implementation against the source.

| Capability | Implemented by | Source |
|---|---|---|
| Geodesic distance, bearing, area | PROJ, via `pyproj.Geod` | Karney, C. F. F. (2013), "Algorithms for geodesics", *Journal of Geodesy* 87(1), 43–55 |
| Datum and projection transforms | PROJ | The EPSG Geodetic Parameter Dataset |
| USNG / MGRS grid references | `pygeodesy` | FGDC-STD-011-2001, the United States National Grid; the same squares are MGRS outside the US |
| Polygon overlay, buffering, hulls | GEOS, via `shapely` | The OGC Simple Features specification |
| Line simplification | GEOS | Douglas, D. H. & Peucker, T. K. (1973), *The Canadian Cartographer* 10(2), 112–122 |
| Hexagonal spatial index | `h3` | Uber H3 |
| Geohash | `geohashr` | Niemeyer, G. (2008) |
| Plus Codes | `pluscodes` | Google Open Location Code specification |
| Free-space path loss | this package | ITU-R P.525, "Calculation of free-space attenuation" |
| Okumura-Hata path loss | this package | Hata, M. (1980), *IEEE Trans. Veh. Technol.* 29(3), 317–325; after Okumura et al. (1968) |
| COST 231-Hata path loss | this package | COST Action 231 final report (1999), ch. 4 |
| Two-ray ground reflection | this package | Standard textbook propagation model |
| Slope, aspect, hillshade | this package | Horn, B. K. P. (1981), "Hill shading and the reflectance map", *Proc. IEEE* 69(1), 14–47 |
| Terrarium DEM decode | this package | Mapzen Terrain Tiles published encoding |
| Sun and moon position | `astral` | Standard astronomical almanac formulae |
| DBSCAN clustering | `scikit-learn` | Ester, M. et al. (1996), *KDD-96* |
| HDBSCAN clustering | `scikit-learn` | Campello, R. J. G. B. et al. (2013), *PAKDD* |

"Implemented by: this package" means the formula was written out from the cited
publication in [`geo/rf.py`](src/geospatial_mcp/geo/rf.py) or
[`geo/terrain.py`](src/geospatial_mcp/geo/terrain.py), because no permissively
licensed Python library implements it. The mathematics is not ours; only the
transcription is.

### What this package actually adds

Given the above, it is worth being plain about what is left:

- **The composition** — which capabilities belong in one server, and what the
  tool surface should look like for a model rather than a human.
- **Correct units and datums at the seams.** Geodesic buffering via an azimuthal
  equidistant reprojection, geodesic path sampling, geodesic area — the places
  where naive degree arithmetic silently gives wrong answers.
- **A shared HTTP layer** with per-host rate limiting, a disk cache, bounded
  retries and mirror failover, so the free public services are used within their
  stated policies.
- **Error design** — tools raise rather than returning an error payload, and
  every message names the input, the problem and a valid example.
- **Attribution plumbing**, so licence obligations travel with the data.
- **Tests and evals** — 293 tests and a 31-case tool-selection eval library.

Full licence texts and obligations are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). If you spot a missing or
incorrect attribution, please open an issue — that is a bug.

## Licence

MIT — see [LICENSE](LICENSE).
