"""The opt-in tool-call trace: complete, harmless, and off by default.

Agent evals score what a model called from this file, whichever harness ran
the model. So it must record failed and malformed calls as faithfully as good
ones, must not change any result, and must never write to stdout.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from mcp import Client

from geospatial_mcp import config
from geospatial_mcp import trace as trace_module
from geospatial_mcp.server import build_server
from geospatial_mcp.trace import PREVIEW_LIMIT, enable_tracing


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _call(server, tool: str, arguments: dict):
    async with Client(server, raise_exceptions=False) as client:
        return await client.call_tool(tool, arguments)


class TestTraceRecords:
    async def test_a_successful_call_is_recorded_with_its_arguments(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)

        await _call(server, "coord_convert", {"location": "30SUF6542755114"})

        (record,) = _lines(trace)
        assert record["tool"] == "coord_convert"
        assert record["arguments"] == {"location": "30SUF6542755114"}
        assert record["ok"] is True
        assert record["result_chars"] > 0
        assert record["duration_ms"] >= 0

    async def test_a_tool_error_is_recorded_as_a_failure_with_the_reason(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)

        result = await _call(
            server,
            "geom_transform",
            {
                "geojson": '{"type":"Point","coordinates":[2,48]}',
                "operation": "buffer",
                "distance_m": 0.0674,
            },
        )

        assert result.is_error  # tracing must not swallow the error
        (record,) = _lines(trace)
        assert record["ok"] is False
        assert "degrees" in record["error"]

    async def test_a_malformed_call_is_recorded_before_validation(self, tmp_path):
        """Argument validity is an eval metric, so rejected arguments must be kept raw."""
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)

        await _call(server, "rf_link_budget", {"frequency_mhz": "not a number"})

        (record,) = _lines(trace)
        assert record["ok"] is False
        assert record["arguments"] == {"frequency_mhz": "not a number"}

    async def test_calls_append_in_order(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)

        async with Client(server, raise_exceptions=False) as client:
            await client.call_tool("coord_convert", {"location": "48.8584,2.2945"})
            await client.call_tool("rf_link_budget", {})

        assert [r["tool"] for r in _lines(trace)] == ["coord_convert", "rf_link_budget"]


class TestTraceIsHarmless:
    async def test_results_are_identical_with_and_without_tracing(self, tmp_path):
        plain = await _call(build_server("default"), "rf_link_budget", {"environment": "rural"})
        traced_server = build_server("default")
        enable_tracing(traced_server, tmp_path / "trace.jsonl")
        traced = await _call(traced_server, "rf_link_budget", {"environment": "rural"})
        assert traced.structured_content == plain.structured_content

    async def test_nothing_reaches_stdout(self, tmp_path, capsys):
        server = build_server("default")
        enable_tracing(server, tmp_path / "trace.jsonl")
        await _call(server, "coord_convert", {"location": "48.8584,2.2945"})
        assert capsys.readouterr().out == ""

    async def test_an_unwritable_trace_does_not_fail_the_call(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)
        trace.mkdir()  # the path is now a directory, so every append fails

        result = await _call(server, "rf_link_budget", {})
        assert not result.is_error


class TestResultPreview:
    """GEO_TRACE_RESULTS adds a few headline values, for a person watching."""

    async def _record(self, tmp_path, tool: str, arguments: dict) -> dict:
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace, results=True)
        await _call(server, tool, arguments)
        (record,) = _lines(trace)
        return record

    async def test_no_preview_unless_asked_for(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace)
        await _call(server, "rf_link_budget", {})
        assert "preview" not in _lines(trace)[0]

    async def test_the_headline_numbers_are_carried(self, tmp_path):
        record = await self._record(
            tmp_path, "rf_link_budget", {"frequency_mhz": 1800, "environment": "suburban"}
        )
        preview = record["preview"]
        assert preview["cell_radius_km"] == 3.2055
        assert preview["max_allowable_path_loss_db"] == 160.0
        assert preview["cached"] is False
        assert "inputs" not in preview
        assert "meta" not in preview

    async def test_the_first_warning_is_carried_whole(self, tmp_path):
        """The validity warning used to end '(1500-2000); tr'. The advice is the point."""
        record = await self._record(
            tmp_path, "rf_link_budget", {"frequency_mhz": 2600, "environment": "suburban"}
        )
        warning = record["preview"]["warning"]
        assert warning.startswith("frequency_mhz=2600 is outside the published validity range")
        assert warning.endswith("(1500-2000); treat the result as indicative only.")

    async def test_the_first_note_is_carried_whole(self, tmp_path):
        record = await self._record(tmp_path, "coord_convert", {"location": "30SUF6542755114"})
        preview = record["preview"]
        assert preview["input"] == "30SUF6542755114"
        assert preview["note"].startswith("A USNG/MGRS reference names the south-west corner")
        assert preview["note"].endswith("this one is 1 m on a side.")

    async def test_a_long_note_is_cut_between_words(self, tmp_path):
        """The OSTN15 note runs past 200 characters, so it is cut, but not mid-word."""
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace, results=True)
        result = await _call(
            server,
            "coord_transform_crs",
            {"x": 530000, "y": 180000, "source_crs": "EPSG:27700", "target_crs": "EPSG:4326"},
        )
        full = result.structured_content["meta"]["notes"][0]
        preview = _lines(trace)[0]["preview"]
        note = preview["note"]

        assert len(full) > 200
        assert 150 < len(note) <= 200
        assert note.endswith("…")
        kept = note.removesuffix("…").rstrip()
        assert full.startswith(kept)
        assert full[len(kept)] == " ", "cut mid-word"
        assert len(json.dumps(preview)) <= PREVIEW_LIMIT

    async def test_a_buffer_previews_its_area_and_never_its_geometry(self, tmp_path):
        record = await self._record(
            tmp_path,
            "geom_transform",
            {
                "geojson": '{"type":"Point","coordinates":[-4.5,36.6]}',
                "operation": "buffer",
                "distance_m": 3210,
            },
        )
        preview = record["preview"]
        assert abs(preview["area_km2"] - 32.36) < 0.1
        assert "geometry" not in preview
        assert "coordinates" not in json.dumps(preview)

    async def test_a_failed_call_has_no_preview(self, tmp_path):
        record = await self._record(tmp_path, "rf_link_budget", {"frequency_mhz": "not a number"})
        assert record["ok"] is False
        assert "preview" not in record

    async def test_results_are_identical_with_the_preview_on(self, tmp_path):
        plain = await _call(build_server("default"), "rf_link_budget", {"environment": "rural"})
        traced_server = build_server("default")
        enable_tracing(traced_server, tmp_path / "trace.jsonl", results=True)
        traced = await _call(traced_server, "rf_link_budget", {"environment": "rural"})
        assert traced.structured_content == plain.structured_content

    async def test_a_broken_preview_does_not_fail_the_call(self, tmp_path, monkeypatch):
        def broken(result):
            raise RuntimeError("preview bug")

        monkeypatch.setattr(trace_module, "_preview", broken)
        trace = tmp_path / "trace.jsonl"
        server = build_server("default")
        enable_tracing(server, trace, results=True)
        result = await _call(server, "rf_link_budget", {})
        assert not result.is_error
        assert _lines(trace)[0]["preview"] is None


class TestPreviewContents:
    """The selection rules, on hand-built results shaped like the real ones."""

    def test_the_first_match_names_the_place(self):
        result = SimpleNamespace(
            structured_content={
                "query": "Palacio de Congresos, Torremolinos",
                "provider": "nominatim",
                "matches": [
                    {"display_name": "Palacio de Congresos y Exposiciones de la Costa del Sol"},
                    {"display_name": "Somewhere else"},
                ],
                "meta": {"cached": True, "notes": []},
            }
        )
        preview = trace_module._preview(result)
        assert preview["match"] == "Palacio de Congresos y Exposiciones de la Costa del Sol"
        assert preview["cached"] is True
        assert "matches" not in preview

    def test_geometry_text_is_left_out(self):
        result = SimpleNamespace(
            structured_content={
                "distance_km": 12.5,
                "geometry_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                "output": "POLYGON ((-4.5 36.6, -4.4 36.6, -4.4 36.7, -4.5 36.6))",
                "wkt": "POINT (-4.5 36.6)",
                "meta": {"cached": False},
            }
        )
        assert trace_module._preview(result) == {"distance_km": 12.5, "cached": False}

    def test_a_large_result_is_cut_to_the_limit_keeping_the_extras(self):
        fields = {f"field_{i:03d}": "x" * 60 for i in range(50)}
        result = SimpleNamespace(
            structured_content={
                **fields,
                "matches": [{"display_name": "Calle Larga, " * 20}],
                "warnings": ["the warning goes on " * 20],
                "meta": {"cached": False, "notes": ["and the note goes on " * 20]},
            }
        )
        preview = trace_module._preview(result)
        assert PREVIEW_LIMIT <= 600
        assert len(json.dumps(preview)) <= PREVIEW_LIMIT
        assert len(preview["match"]) <= 100
        assert 180 < len(preview["warning"]) <= 200
        assert preview["cached"] is False
        # The note is shortened, between words, so a headline value still fits.
        assert 40 <= len(preview["note"]) < 180
        assert preview["note"].endswith("…")
        assert "field_000" in preview

    def test_a_long_warning_and_note_fit_whole_beside_the_numbers(self):
        """Shaped like rf_link_budget: the reserve must not shorten a typical pair."""
        result = SimpleNamespace(
            structured_content={
                "cell_radius_km": 2.2255,
                "cell_area_km2": 12.8671,
                "warnings": ["w " * 95],
                "meta": {"cached": False, "notes": ["n " * 95]},
            }
        )
        preview = trace_module._preview(result)
        assert preview["warning"] == "w " * 95
        assert preview["note"] == "n " * 95
        assert preview["cell_radius_km"] == 2.2255
        assert len(json.dumps(preview)) <= PREVIEW_LIMIT

    def test_text_is_cut_between_words_with_an_ellipsis(self):
        warning = "the warning goes on " * 20
        clipped = trace_module._clip(warning, 200)
        assert 180 < len(clipped) <= 200
        assert clipped.endswith("…")
        assert warning.startswith(clipped[:-1])
        assert warning[len(clipped) - 1] == " ", "cut mid-word"
        assert all(word in {"the", "warning", "goes", "on"} for word in clipped[:-1].split())

    def test_a_cut_after_a_full_stop_keeps_it(self):
        text = "First sentence is here. Second one."
        assert trace_module._clip(text, 26) == "First sentence is here. …"
        assert trace_module._clip(text, 33) == "First sentence is here. Second…"

    def test_a_long_unbroken_string_is_cut_at_the_limit(self):
        name = "uk_os_OSTN15_NTv2_OSGBtoETRS" * 10
        clipped = trace_module._clip(name, 100)
        assert len(clipped) <= 100
        assert clipped.endswith("…")
        assert name.startswith(clipped[:-1])
        assert len(clipped) >= 98

    def test_short_text_and_numbers_are_left_alone(self):
        assert trace_module._clip("indicative only.", 200) == "indicative only."
        assert trace_module._clip(3.2055, 10) == 3.2055

    def test_a_bare_model_is_previewed_too(self):
        """Called without convert_result, a tool returns its pydantic model."""
        from pydantic import BaseModel

        class Elevation(BaseModel):
            elevation_m: float
            dataset: str

        preview = trace_module._preview(Elevation(elevation_m=771.0, dataset="Copernicus GLO-90"))
        assert preview == {"elevation_m": 771.0, "dataset": "Copernicus GLO-90"}


class TestTraceSwitch:
    def test_off_by_default(self):
        server = build_server("default")
        # Every tool's run is wrapped once to name rate-limited calls (server.py); none by the tracer.
        runs = [vars(t).get("run") for t in server._tool_manager.list_tools()]
        assert not any(getattr(r, "__name__", "") == "traced_run" for r in runs)

    async def test_geo_trace_file_turns_it_on(self, tmp_path):
        trace = tmp_path / "from-env.jsonl"
        original = config.settings
        config.set_settings(replace(original, trace_file=trace))
        try:
            server = build_server("default")
        finally:
            config.set_settings(original)

        await _call(server, "rf_link_budget", {})
        assert _lines(trace)[0]["tool"] == "rf_link_budget"

    def test_settings_read_the_variable(self, tmp_path):
        settings = config.Settings.from_env({"GEO_TRACE_FILE": str(tmp_path / "t.jsonl")})
        assert settings.trace_file == tmp_path / "t.jsonl"
        assert config.Settings.from_env({}).trace_file is None

    def test_the_preview_is_off_unless_geo_trace_results_is_set(self):
        assert config.Settings.from_env({}).trace_results is False
        assert config.Settings.from_env({"GEO_TRACE_RESULTS": "0"}).trace_results is False
        assert config.Settings.from_env({"GEO_TRACE_RESULTS": "1"}).trace_results is True

    async def test_geo_trace_results_turns_the_preview_on(self, tmp_path):
        trace = tmp_path / "from-env.jsonl"
        original = config.settings
        config.set_settings(replace(original, trace_file=trace, trace_results=True))
        try:
            server = build_server("default")
        finally:
            config.set_settings(original)

        await _call(server, "rf_link_budget", {})
        assert "cell_radius_km" in _lines(trace)[0]["preview"]
