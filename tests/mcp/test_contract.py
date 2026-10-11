"""Contract tests for the agent-facing surface.

These guard the things that silently degrade an agent's ability to use the
server: tool naming, description quality, schema size, error signalling and
toolset gating. They are cheap and none of them touch the network.
"""

from __future__ import annotations

import json
import re

import pytest

from geospatial_mcp.tools import TOOLSETS, resolve_toolsets

from ..conftest import mcp_client

#: Domain prefixes a tool name may use. Keeps related tools adjacent when the
#: catalogue is listed alphabetically, and gives the model a retrieval cue.
NAME_PATTERN = re.compile(
    r"^(geo|coord|geom|place|route|terrain|env|sun|time|data|rf|orbit|hazard|track|stats|raster)_[a-z0-9_]+$"
)

#: Hard ceiling on the serialised default catalogue. Every tool definition is
#: re-sent on every turn, so this is a recurring context cost, not a one-off.
#: The default catalogue measures about 40,000; the headroom is room for a few
#: more tools, not for letting existing ones grow.
SCHEMA_BUDGET_CHARS = 50_000

#: An install command whose package is this server's name alone, with or
#: without extras, rather than a git source. Neither name is this server's on
#: PyPI: geospatial-mcp is an unrelated project, and mcp-geospatial is unowned.
BARE_INSTALL = re.compile(
    r"\b(?:pip install|pipx install|uv tool install|uv add|uv pip install|uvx(?: --from| --with)?)"
    r"\s+['\"]?(?:mcp-geospatial|geospatial-mcp)(?:\[[\w,]*\])?(?!\s*@)['\"]?(?=\s|$|`)"
)


class TestNaming:
    async def test_every_tool_follows_the_naming_convention(self):
        async with mcp_client() as client:
            tools = (await client.list_tools()).tools
            offenders = [t.name for t in tools if not NAME_PATTERN.match(t.name)]
            assert not offenders, f"tool names outside the convention: {offenders}"

    async def test_no_vague_verb_prefixes(self):
        """`get_` and `calculate_` carry no information and crowd the namespace."""
        async with mcp_client() as client:
            tools = (await client.list_tools()).tools
            offenders = [
                t.name for t in tools if t.name.startswith(("get_", "calculate_", "do_", "run_"))
            ]
            assert not offenders, offenders

    async def test_the_default_catalogue_is_the_documented_size(self):
        async with mcp_client() as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 22, [t.name for t in tools]


class TestDescriptions:
    async def test_every_tool_has_a_substantive_description(self):
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                description = tool.description or ""
                assert len(description) >= 80, f"{tool.name} description is too thin"
                assert len(description) <= 2000, f"{tool.name} description is bloated"

    async def test_first_line_is_a_standalone_summary(self):
        """Some clients show only the first line in a picker."""
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                first = (tool.description or "").split("\n")[0].strip()
                assert first.endswith("."), f"{tool.name}: first line is not a sentence"
                assert 20 <= len(first) <= 200, f"{tool.name}: first line is {len(first)} chars"

    async def test_every_parameter_is_described(self):
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                properties = (tool.input_schema or {}).get("properties") or {}
                undocumented = [
                    name for name, spec in properties.items() if not spec.get("description")
                ]
                assert not undocumented, f"{tool.name} has undocumented parameters: {undocumented}"

    async def test_neighbouring_tools_are_disambiguated(self):
        """Tools with close neighbours must say when to use the other one."""
        async with mcp_client() as client:
            # Pairs, not a dict: a tool can have several neighbours, and a dict
            # literal with a repeated key silently keeps only the last one.
            expectations = [
                ("geom_measure", "route_directions"),
                ("route_directions", "geom_measure"),
                ("route_directions", "route_matrix"),
                ("route_isochrone", "route_directions"),
                ("route_matrix", "route_directions"),
                ("route_matrix", "route_isochrone"),
                ("place_geocode", "place_search"),
                ("place_search", "place_geocode"),
                ("coord_convert", "coord_transform_crs"),
                ("terrain_profile", "terrain_elevation"),
                ("terrain_elevation", "terrain_profile"),
                ("rf_link_budget", "rf_towers"),
            ]
            tools = {t.name: (t.description or "") for t in (await client.list_tools()).tools}
            for name, must_mention in expectations:
                assert must_mention in tools[name], (
                    f"{name} should tell the model when to use {must_mention} instead"
                )


class TestAnnotations:
    async def test_nothing_claims_to_be_destructive(self):
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                annotations = tool.annotations
                assert annotations is not None, tool.name
                assert annotations.read_only_hint is True, tool.name
                assert annotations.destructive_hint is False, tool.name

    async def test_offline_tools_are_marked_closed_world(self):
        """openWorldHint distinguishes pure maths from a live external call."""
        async with mcp_client() as client:
            expected_offline = {
                "coord_convert",
                "coord_describe",
                "coord_transform_crs",
                "geom_transform",
                "geom_overlay",
                "geom_relate",
                "sun_moon",
                "time_at_location",
                "data_convert",
                "geo_capabilities",
                "rf_link_budget",
            }
            for tool in (await client.list_tools()).tools:
                if tool.name in expected_offline:
                    assert tool.annotations.open_world_hint is False, tool.name


class TestSchemaBudget:
    async def test_default_catalogue_fits_the_context_budget(self):
        async with mcp_client() as client:
            tools = (await client.list_tools()).tools
            payload = json.dumps(
                [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]
            )
            assert len(payload) < SCHEMA_BUDGET_CHARS, (
                f"tools/list is {len(payload):,} chars, over the {SCHEMA_BUDGET_CHARS:,} budget. "
                "Trim descriptions or response models before adding more tools."
            )

    async def test_descriptions_carry_no_source_indentation(self):
        """Docstring indentation is whitespace the model reads on every turn."""
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                indented = [
                    line for line in (tool.description or "").splitlines() if line.startswith(" ")
                ]
                assert not indented, f"{tool.name}: {indented[0]!r}"

    async def test_schemas_carry_no_redundant_titles(self):
        """Pydantic emits `title: 'Distance M'` for `distance_m`. Pure restatement."""
        async with mcp_client() as client:
            for tool in (await client.list_tools()).tools:
                for schema in (tool.input_schema, tool.output_schema):
                    if schema:
                        assert "title" not in json.dumps(schema), tool.name

    def test_server_instructions_fit_the_client_limit(self):
        """Claude Code truncates server instructions at 2 KB."""
        from geospatial_mcp.server import INSTRUCTIONS

        assert len(INSTRUCTIONS.encode("utf-8")) <= 2048, len(INSTRUCTIONS)


class TestToolsetGating:
    def test_default_alias_resolves_to_core_and_rf(self):
        assert resolve_toolsets("default") == {"core", "rf"}

    def test_all_alias_includes_everything(self):
        assert resolve_toolsets("all") == set(TOOLSETS)

    def test_explicit_selection_narrows_the_catalogue(self):
        assert resolve_toolsets("core") == {"core"}

    def test_unknown_toolset_names_the_valid_options(self):
        with pytest.raises(ValueError) as exc:
            resolve_toolsets("nonsense")
        message = str(exc.value)
        assert "core" in message and "default" in message

    async def test_core_only_omits_the_rf_tools(self):
        async with mcp_client("core") as c:
            names = {t.name for t in (await c.list_tools()).tools}
        assert not any(n.startswith("rf_") for n in names)
        assert "coord_convert" in names

    async def test_capabilities_reports_what_is_switched_off(self):
        async with mcp_client("core") as c:
            result = await c.call_tool("geo_capabilities", {})
        body = result.structured_content
        assert body["enabled_toolsets"] == ["core"]
        disabled = [t for t in body["toolsets"] if not t["enabled"]]
        assert any(t["name"] == "rf" for t in disabled)
        assert "GEO_TOOLSETS" in body["how_to_enable"]


class TestGeometryArgumentInterop:
    """Geometry parameters must accept both a JSON object and a JSON string.

    Regression test. The MCP SDK parses any JSON-looking string argument into a
    Python object before validating it against the tool signature. A parameter
    declared ``str`` that receives ``'{"type":"Point",...}'`` is therefore
    handed a ``dict`` and rejected with "Input should be a valid string" --
    despite the client having sent exactly what the description asked for.
    Models also legitimately send GeoJSON as a nested object.
    """

    POLYGON_TEXT = (
        '{"type":"Polygon","coordinates":'
        "[[[0.118,52.203],[0.128,52.203],[0.128,52.210],[0.118,52.210],[0.118,52.203]]]}"
    )

    @pytest.mark.parametrize("as_object", [False, True])
    @pytest.mark.parametrize(
        ("tool", "build"),
        [
            ("geom_measure", lambda g: {"origin": "52.2053,0.1218", "geojson": g}),
            ("geom_transform", lambda g: {"geojson": g, "operation": "centroid"}),
            (
                "geom_overlay",
                lambda g: {"geojson_a": g, "geojson_b": g, "operation": "intersection"},
            ),
            ("geom_relate", lambda g: {"geojson_a": g, "geojson_b": g}),
            ("data_convert", lambda g: {"data": g, "to_format": "wkt"}),
        ],
    )
    async def test_both_shapes_are_accepted(self, tool, build, as_object):
        geometry = json.loads(self.POLYGON_TEXT) if as_object else self.POLYGON_TEXT
        async with mcp_client() as client:
            result = await client.call_tool(tool, build(geometry))
        assert not result.is_error, (
            f"{tool} rejected geometry passed as "
            f"{'an object' if as_object else 'a string'}: "
            + "".join(getattr(b, "text", "") for b in (result.content or []))[:200]
        )

    async def test_wkt_still_works_where_geojson_is_accepted(self):
        async with mcp_client() as client:
            result = await client.call_tool(
                "geom_transform",
                {"geojson": "POINT (2.29 48.86)", "operation": "buffer", "distance_m": 100},
            )
        assert not result.is_error


class TestOfflinePreset:
    """The `offline` alias must filter tools, not toolsets.

    `core` and `rf` each mix network and offline tools, so selecting only
    wholly-offline toolsets would leave almost nothing usable. The alias
    therefore enables everything and then removes the networked tools.
    """

    async def test_offline_preset_has_no_networked_tools(self):
        async with mcp_client("offline") as client:
            tools = (await client.list_tools()).tools
        assert tools, "the offline preset must not be empty"
        networked = [t.name for t in tools if t.annotations.open_world_hint]
        assert not networked, f"offline preset still exposes: {networked}"

    async def test_offline_preset_keeps_the_useful_offline_tools(self):
        async with mcp_client("offline") as client:
            names = {t.name for t in (await client.list_tools()).tools}
        for expected in (
            "coord_convert",
            "geom_transform",
            "geom_overlay",
            "sun_moon",
            "time_at_location",
            "data_convert",
            "rf_link_budget",
        ):
            assert expected in names, f"{expected} works offline but was filtered out"

    async def test_offline_preset_is_smaller_than_the_full_catalogue(self):
        async with mcp_client("offline") as offline, mcp_client("all") as full:
            offline_count = len((await offline.list_tools()).tools)
            full_count = len((await full.list_tools()).tools)
        assert 0 < offline_count < full_count


class TestExtrasGating:
    def test_toolsets_declare_the_extra_they_need(self):
        assert TOOLSETS["analysis"].extra == "stats"
        assert TOOLSETS["core"].extra is None
        assert TOOLSETS["rf"].extra is None

    def test_default_toolsets_need_no_optional_dependency(self):
        """The default catalogue must work from a bare `pip install`."""
        for name, toolset in TOOLSETS.items():
            if toolset.default_on:
                assert toolset.extra is None, f"{name} is on by default but needs an extra"

    def test_missing_extra_fails_at_startup_not_on_first_call(self, monkeypatch):
        """Registration alone does not prove a toolset is usable.

        Tool modules import heavy optional dependencies lazily inside the
        function body, so without a probe a toolset whose extra is absent would
        register cleanly and fail only when a user called the tool, deep inside a
        conversation. The probe moves that failure to startup.
        """
        import importlib.util

        from geospatial_mcp.tools import _require_extra

        real_find_spec = importlib.util.find_spec

        def missing_sklearn(name, *args, **kwargs):
            if name == "sklearn":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", missing_sklearn)
        with pytest.raises(RuntimeError) as exc:
            _require_extra(TOOLSETS["analysis"])
        message = str(exc.value)
        assert "stats" in message
        assert "pip install" in message, "the error must carry the command that fixes it"
        assert "git+https://github.com/ystoneman/geospatial-mcp" in message

    def test_install_commands_fetch_this_repository_not_the_pypi_name(self):
        """The server is not on PyPI, and the PyPI geospatial-mcp is an unrelated project.

        The package and command names are read from pyproject.toml, so a rename
        that misses the install helper fails here.
        """
        import tomllib
        from pathlib import Path

        from packaging.requirements import Requirement

        from geospatial_mcp.errors import GeoUnavailable, install_commands

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        (command,) = project["scripts"]
        uvx, pip = install_commands("stats")
        requirement = Requirement(pip.removeprefix("pip install ").strip("'"))
        assert requirement.name == project["name"]
        assert requirement.extras == {"stats"}
        assert requirement.url == "git+https://github.com/ystoneman/geospatial-mcp"
        assert uvx.startswith("uvx --from ") and uvx.endswith(f" {command}")
        assert uvx in str(GeoUnavailable("Clustering", "stats"))

    def test_no_install_line_names_a_bare_package(self):
        """`pip install mcp-geospatial` would fetch whatever owns that name on PyPI."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        named = ("README.md", "CONTRIBUTING.md", "AGENTS.md", "CHANGELOG.md", "Dockerfile")
        files = [
            *(root / name for name in (*named, "Makefile", "railway.toml", "evals/README.md")),
            *(root / "docs").glob("*.md"),
            *(root / "src").rglob("*.py"),
            *(root / ".github").rglob("*.yml"),
        ]
        offenders = [
            f"{path.relative_to(root)}:{number}: {line.strip()}"
            for path in files
            if path.is_file()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if BARE_INSTALL.search(line)
        ]
        assert not offenders, "install from git instead:\n" + "\n".join(offenders)

    @pytest.mark.parametrize(
        ("line", "bare"),
        [
            ("pip install mcp-geospatial", True),
            ("uvx mcp-geospatial --toolsets all", True),
            ("uvx geospatial-mcp", True),
            ("pip install 'mcp-geospatial[stats]'", True),
            ("uvx --with 'mcp-geospatial[stats]' mcp-geospatial", True),
            ("uv tool install mcp-geospatial", True),
            ("uvx --from git+https://github.com/ystoneman/geospatial-mcp mcp-geospatial", False),
            ("pip install git+https://github.com/ystoneman/geospatial-mcp", False),
            (
                "pip install 'mcp-geospatial[stats] @ git+https://github.com/ystoneman/geospatial-mcp'",
                False,
            ),
            ("mcp-geospatial --toolsets core", False),
        ],
    )
    def test_the_bare_install_pattern_fires_only_on_bare_names(self, line, bare):
        assert bool(BARE_INSTALL.search(line)) is bare

    def test_server_json_lists_no_pypi_package(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        server = json.loads((root / "server.json").read_text(encoding="utf-8"))
        assert server["repository"]["url"] == "https://github.com/ystoneman/geospatial-mcp"
        assert not [p for p in server.get("packages", []) if p.get("registryType") == "pypi"]

    def test_toolsets_without_an_extra_never_probe(self):
        from geospatial_mcp.tools import _require_extra

        for name in ("core", "rf"):
            _require_extra(TOOLSETS[name])  # must not raise
