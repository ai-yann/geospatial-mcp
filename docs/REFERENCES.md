# References

Almost none of the geospatial computation in this package is original to it.
[The README's credits section](../README.md#sources-and-credits) covers what the
code *depends on*; this file covers what a reader might want to go and look at
next — other servers in this space, the standards the tools implement, and the
research on what LLM agents can and cannot currently do with geospatial tools.

**A note on numbers.** A figure that is a *property of an artifact* is safe to
write down — GeoAnalystBench has 50 tasks, GISAgentBench has 349, COPC is LAZ
1.4. A figure that is a *measurement of a moving target* is not: leaderboard
scores, star counts, registry totals, prices and "the current version" all rot,
and several of them rotted between this file being drafted and being checked. So
none of those are reproduced here; the link is the authority and this page is
not. Everything below was verified against a primary source, and re-verified
adversarially, on 2026-08-29.

## Finding other geospatial MCP servers

Start here rather than with this file — these are maintained, and this file is a
snapshot.

- **[sparkgeo/geo-mcp-servers](https://github.com/sparkgeo/geo-mcp-servers)** —
  a curated, machine-checked index of geospatial MCP servers, categorised
  (geocoding, routing, maps, database/analytics, Earth observation, weather,
  desktop GIS, GIS toolkits, data catalogues, aviation/maritime) and marked for
  liveness, so a stale project is visibly stale. MIT licensed, and the data
  lives in YAML you can parse rather than prose you have to read. What sets it
  apart from the other geospatial lists below is that its liveness marks are
  machine-checked in CI rather than hand-maintained. Third-party write-up:
  [a listing of geospatial MCP servers](https://spatialists.ch/posts/2026/08/12-listing-of-geospatial-mcp-servers/).
- **[BENZEMA216/awesome-map-mcp](https://github.com/BENZEMA216/awesome-map-mcp)**
  — a larger hand-curated list covering maps, LBS, routing, POI and location
  intelligence, with published inclusion rules and a generated index.
- **[KeynesYouDigIt/awesome-geospatial-mcp](https://github.com/KeynesYouDigIt/awesome-geospatial-mcp)**
  — a shorter CC0 list that is explicit that entries carry no endorsement.
- **[honua-io/geospatial-mcp](https://github.com/honua-io/geospatial-mcp)** — not
  a list but a draft *standard*: a vendor-neutral vocabulary of tool names and
  JSON Schemas for geospatial MCP servers, with conformance fixtures and a
  governance process. Worth watching if you care whether these servers ever
  agree on what a tool is called. Apache-2.0. (It shares this repository's name
  and is unrelated to it.)
- **[The official MCP registry](https://registry.modelcontextprotocol.io/v0/servers?search=geospatial)**
  — searchable API, broader than geospatial, authoritative for what has actually
  been published. Worth knowing that none of these indexes is complete: a
  capability buried in a tool list rather than a one-line description is
  invisible to all of them.
- **[gis-mcp's related-servers page](https://github.com/mahdin75/gis-mcp/blob/main/docs/related-mcp-servers.md)**
  — another project's view of the same field, which is a useful cross-check on
  ours.

## Servers that do things this one does not

Not a ranking — a map of where to go when this package is the wrong tool. Check
the licence column before redistributing anything.

| Server | Go here instead when you need | Licence |
|---|---|---|
| [mapbox/mcp-server](https://github.com/mapbox/mcp-server) | Commercial-grade geocoding, POI search, matrices, map matching, optimisation and rendered maps. Needs a Mapbox token; the data is not open. | MIT (server) |
| [nkarasiak/qgis-mcp](https://github.com/nkarasiak/qgis-mcp) | An agent driving a **real QGIS instance** — layers, styling, layouts, the processing framework. Local desktop control, so treat the security model seriously. | GPL-2.0-or-later (plugin) / MIT (server) |
| [mahdin75/gis-mcp](https://github.com/mahdin75/gis-mcp) | A broad Python geoprocessing toolbox: GeoPandas, PySAL, network analysis, plotting. Much wider vector/raster surface than here. | MIT |
| [JordanGunn/gdal-mcp](https://github.com/JordanGunn/gdal-mcp) | Raw GDAL — format translation and raster/vector wrangling beyond what this package's `data_convert` covers. | MIT |
| [nickoulos/mcp-ogc](https://github.com/nickoulos/mcp-ogc) | Existing OGC web services (WMS/WFS) — the pre-LLM interoperability stack. **AGPL-3.0**, which has real consequences if you offer it as a network service. | AGPL-3.0 |
| [planetlabs/planet-mcp](https://github.com/planetlabs/planet-mcp) | Satellite imagery discovery and **ordering**. Note it can spend money; Planet labels it experimental beta. | see repo |
| [CesiumGS/cesium-ai-integrations](https://github.com/CesiumGS/cesium-ai-integrations) | 3D globes, terrain and 3D Tiles. **Deprecated** — read it for the architectural lesson (Cesium moved off the local WebSocket bridge), then use what it points you to. | Apache-2.0 |
| [aws-samples/sample-geospatial-kiro-power-pack](https://github.com/aws-samples/sample-geospatial-kiro-power-pack) | Point clouds — a `geo-pointcloud` component reading LAS/LAZ/COPC. The default path is pure-Python laspy/lazrs with no native GDAL or PDAL; PDAL is an optional, write-only backend. Sample-stage, but the clearest published shape for a point-cloud MCP. | MIT-0 |

Point clouds are the thinnest part of the field. [COPC](https://copc.io/) stores
an octree-organised point cloud inside a single LAZ 1.4 file and supports
range-reads, so an agent can fetch a spatial subset instead of ingesting
gigabytes — a good substrate for a server nobody has built properly yet.

## Standards and specifications this package implements

| Area | Specification |
|---|---|
| Protocol | [Model Context Protocol](https://modelcontextprotocol.io/specification/) — this server targets revision `2026-07-28`, which made the core stateless: protocol sessions, the `Mcp-Session-Id` header and the `initialize` handshake were all removed |
| Grid references | FGDC-STD-011-2001, the United States National Grid — the civilian name for the same squares MGRS names elsewhere |
| Geodesy | Karney, C. F. F. (2013), "Algorithms for geodesics", *Journal of Geodesy* 87(1), 43–55 — [doi:10.1007/s00190-012-0578-z](https://doi.org/10.1007/s00190-012-0578-z) |
| Free-space path loss | ITU-R P.525, "Calculation of free-space attenuation" |
| Path loss (empirical) | Hata, M. (1980), *IEEE Trans. Veh. Technol.* 29(3), 317–325; COST Action 231 final report, ch. 4 |
| Terrain | Horn, B. K. P. (1981), "Hill shading and the reflectance map", *Proc. IEEE* 69(1), 14–47 |
| Formats | [GeoJSON (RFC 7946)](https://datatracker.ietf.org/doc/html/rfc7946), OGC Simple Features, [STAC](https://stacspec.org/) |

## Research: what agents can actually do with geospatial tools

Two to read first, because they pull in opposite directions and the tension
between them is the whole point.

**[GISAgentBench](https://arxiv.org/abs/2608.01645)** (Aug 2026) evaluates agents
on 349 multi-step tasks drawn from GIS Stack Exchange, scored by comparing
actual output artifacts against executable ground truth with tolerance-aware
rules — not by asking a model whether the code looks right. The best agent
scored **32.7%** under strict scoring, with CRS, topology, numeric attributes
and incomplete result sets the dominant failure modes. Read the authors' own
caveat alongside it: most models produced output *close* to ground truth, so
this is a measure of exactness rather than of hopelessness. That is the point —
close is not the same as correct when the answer is a coordinate.

**[Mapbox: "From answers to addresses"](https://www.mapbox.com/blog/from-answers-to-addresses-grounding-an-llm-for-location)**
(Aug 2026) is the other half: a structured location API beat web-grounded
retrieval on accuracy, tokens and cost in its tests, including a case where two
points 890 m apart in a straight line are a 23-minute walk apart on the network.
It is vendor-authored and should be read as such. It publishes
[runnable scripts](https://github.com/mapbox/location-ai-tutorials/tree/main/location-grounding-part-1),
which is more than most vendor benchmarks manage — but they do not reproduce the
headline accuracy figures: the shipped scripts use 4- and 5-place miniatures and
the 26-place evaluation set is not published. Treat the token and cost ratios as
demonstrable and the accuracy numbers as vendor-internal single runs.

The lineage, for context on how the field got here:

| Work | What it established |
|---|---|
| [Autonomous GIS](https://arxiv.org/abs/2305.06453) (2023) | Named the problem and the prototype (LLM-Geo): an LLM that plans, generates and executes geospatial code |
| [GeoLLM](https://arxiv.org/abs/2310.06213) (2023) | Language models carry real latent geographic knowledge — which is *not* the same as being able to compute with it |
| [MapGPT](https://doi.org/10.1080/15230406.2024.2404868) (2024, *CaGIS*) | Map production itself as an agent workflow. Beware: an unrelated [arXiv paper](https://arxiv.org/abs/2401.07314) shares the name but is about vision-and-language navigation |
| [ShapefileGPT](https://arxiv.org/abs/2410.12376) (2024) | 95.24% on its own benchmark with a planner/worker split and a constrained function library — bounded schemas work |
| [GeoAgent](https://arxiv.org/abs/2410.18792) (2024) | Search and planning over a code interpreter beats single-shot tool calls |
| [GIS Copilot](https://arxiv.org/abs/2411.03205) (2024) | An agent inside QGIS across 110 tasks (60 basic / 30 intermediate / 20 advanced); strong on basic and intermediate, materially harder unguided |
| [GeoBenchX](https://arxiv.org/abs/2503.18129) (2025) | Moved evaluation from free-form code to **tool calling** — the layer MCP standardises |
| [GeoAnalystBench](https://arxiv.org/abs/2509.05881) (2025) | 50 tasks derived from real-world problems and validated by GIS experts; workflow-plausible code is not the same as correct analysis. Published in *Transactions in GIS*, [doi:10.1111/tgis.70135](https://doi.org/10.1111/tgis.70135) |
| [OpenEarthAgent](https://arxiv.org/abs/2602.17665) (2026) | Training on verified tool interactions helps — 55.77% on GIS tasks, ahead of GPT-5 at 54.94% and GPT-4o at 41.95% — and GIS remains the bottleneck category. Cite v4: v1's ordering, still widely quoted, predates GPT-5 and implies a far larger lead |
| [GISclaw](https://arxiv.org/abs/2603.26845) (2026) | Up to 100% and 97% mean on GeoAnalystBench — and, usefully, that adding a second agent can *degrade* a strong model |

Read GISclaw's 97% and GISAgentBench's 32.7% together and the lesson is not that
one is wrong. They use different task sets, scaffolding and correctness
criteria. Decontextualised agent success rates are close to meaningless.

## What this means for how this package is built

The evidence points one way, and it is why the tools here look as they do:

- **The model plans; the engine computes.** Coordinates, projections, topology,
  network cost and geodesic area come from PROJ, GEOS and published formulas —
  never from the model's own arithmetic. See
  [what it gets right that is easy to get wrong](../README.md#what-it-gets-right-that-is-easy-to-get-wrong).
- **Failure modes cluster in CRS, topology, units and completeness.** So
  `coord_transform_crs` reports when PROJ falls back to an approximate datum
  shift, DEM gaps stay `null`, grid references truncate rather than round, and
  empirical models say when they are out of range.
- **Big geometry should not enter the context window.** Results are summarised
  server-side; a caller asks for the geometry when it wants it.
- **An agent that picked the right tool has not necessarily got the right
  answer.** The [eval library](../evals/README.md) measures tool *selection*.
  Output correctness against independent references is a genuine open gap here,
  and GISAgentBench is the reason to care about it.
