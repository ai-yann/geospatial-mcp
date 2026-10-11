# Contributing

## Setup

```bash
git clone https://github.com/ystoneman/geospatial-mcp && cd geospatial-mcp
uv sync --all-extras
make check
```

`make check` runs lint, type-checking and tests — exactly what CI runs. If it
passes locally, CI will pass.

## Adding a tool

The full procedure is in [AGENTS.md](AGENTS.md). In short:

1. Put the computation in `src/geospatial_mcp/geo/` (pure, no I/O) or add a
   provider under `src/geospatial_mcp/net/providers/`.
2. Register the tool in the appropriate `src/geospatial_mcp/tools/` module.
3. Write the docstring as prompt surface — it is what makes the model choose
   correctly. Say what the tool does, what it is for, and **when to use a
   neighbouring tool instead**.
4. Add unit tests and at least one case in `evals/prompts.yaml`.
5. `make check && make evals`.

Contract tests will reject a tool whose name breaks convention, whose
parameters are undocumented, that fails to cross-reference a near neighbour, or
that pushes the catalogue past its context budget.

## Using third-party code

This project is MIT and must stay redistributable under those terms.

**Permitted:** MIT, BSD (2- and 3-clause), Apache-2.0, ISC, public domain,
Python Software Foundation.

**Not permitted:** GPL and AGPL in any version, and anything with a
non-commercial or field-of-use restriction. This rules out several tempting
geospatial libraries — `pycraf`, `pysolar`, `richdem`, `pysheds`,
`pyroutelib3`, `pyorbital`, `pandana`, and `python-igraph` (which transitively
rules out `scikit-mobility` and `mappymatch`).

**Case by case:** LGPL is acceptable only where dynamically linked and
documented. Today that means GEOS, via Shapely — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

If you adapt code from another project:

1. Add a marker comment in the file naming the source:
   `# THIRD-PARTY: <project name>`
2. Add an entry to `THIRD_PARTY_NOTICES.md` with the licence and a link.
3. Keep any notice file the upstream licence requires.

`scripts/check_attribution.sh` fails CI if a marker has no matching entry.

Adding a runtime dependency? Record it in `THIRD_PARTY_NOTICES.md` in the same
commit, and prefer packages with binary wheels for Linux, macOS and Windows on
both x86-64 and ARM — the project's promise is that the `uvx` install in the
README works with no system packages.

## Adding a data provider

Public geospatial APIs are free and shared. A new provider must:

- Set a rate limit in `net/ratelimit.py` matching the provider's stated policy.
- Set a cache TTL in `net/cache.py` reflecting how fast the data really changes.
- Add its attribution string to `attribution.py`, and return it in `meta`.
- Expose a base-URL setting so users can self-host.
- Have a keyless path, or a keyless fallback provider.

## Style

- British spelling in prose; US spelling in identifiers.
- Comments explain *why*, particularly where the obvious approach is wrong.
- Errors must teach recovery. `GeoInputError.with_example(...)` enforces the
  shape: what was received, why it is wrong, what a valid value looks like.

## Reporting bugs

Open an issue with the tool name, the arguments, and what you expected. If it
involves an external provider, say which and include `meta` from the response.
For security issues see [SECURITY.md](SECURITY.md) — do not open a public issue.
