"""The cross-harness runner: transcript parsing, scoring and environment hygiene.

No harness is launched here. The parsers are exercised on recorded transcripts
in ``fixtures/``, and scoring on hand-built traces, so these stay offline.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

import harness

FIXTURES = Path(__file__).parent / "fixtures"

CASE = {
    "id": "telecom-four-site-coverage-005",
    "persona": "telecom",
    "axis": "chain",
    "prompt": "Can four sites cover it?",
    "expect_tools": ["rf_link_budget"],
    "optional_tools": ["geom_transform"],
    "forbid_tools": ["rf_towers"],
    "answer": {"numbers": [{"near": 57.4, "tol": 3}]},
    "max_turns": 8,
}


def _run_with(tmp_path: Path, fixture: str) -> harness.RunPaths:
    run = harness.RunPaths(tmp_path / "run")
    run.create()
    shutil.copy(FIXTURES / fixture, run.transcript)
    return run


class TestParsers:
    def test_opencode(self, tmp_path):
        parsed = harness.OpenCode(None, None, {}).parse(_run_with(tmp_path, "opencode.jsonl"))
        assert parsed.mcp_tools == ["coord_convert"]
        assert parsed.harness_tools == []
        assert "36.632" in parsed.final_text
        assert parsed.turns == 2
        assert parsed.cost_usd == 0
        assert not parsed.error

    def test_claude_code(self, tmp_path):
        parsed = harness.ClaudeCode(None, None, {}).parse(_run_with(tmp_path, "claude-code.jsonl"))
        assert parsed.mcp_tools == ["rf_link_budget"]
        assert parsed.harness_tools == ["Bash"]
        assert parsed.server_connected is True
        assert parsed.final_text.startswith("Four cells")
        assert parsed.cost_usd == pytest.approx(0.0812)

    def test_codex(self, tmp_path):
        parsed = harness.Codex(None, None, {}).parse(_run_with(tmp_path, "codex.jsonl"))
        assert parsed.mcp_tools == ["rf_link_budget"]
        assert parsed.harness_tools == ["command_execution"]
        assert parsed.final_text == "Each cell reaches about 3.21 km."
        assert parsed.tokens == 15350

    def test_an_empty_transcript_is_an_error_not_a_pass(self, tmp_path):
        run = harness.RunPaths(tmp_path / "run")
        run.create()
        for adapter in (harness.OpenCode, harness.ClaudeCode, harness.Codex):
            assert adapter(None, None, {}).parse(run).error


class TestScoring:
    def _parsed(self, text: str, tools: list[str] | None = None) -> harness.Parsed:
        return harness.Parsed(final_text=text, harness_tools=tools or [])

    def test_right_tools_and_right_answer_pass(self, tmp_path):
        trace = [{"tool": "rf_link_budget", "ok": True}, {"tool": "geom_transform", "ok": True}]
        record = harness.score(CASE, "mcp", trace, self._parsed("About 57% covered."), tmp_path)
        assert record["passed"]
        assert record["tools_ok"]
        assert record["first_call_correct"]

    def test_right_tools_wrong_answer_fails(self, tmp_path):
        trace = [{"tool": "rf_link_budget", "ok": True}]
        record = harness.score(CASE, "mcp", trace, self._parsed("About 47% covered."), tmp_path)
        assert not record["passed"]
        assert record["tools_ok"]
        assert record["answer_ok"] is False

    def test_a_forbidden_tool_fails(self, tmp_path):
        trace = [{"tool": "rf_link_budget", "ok": True}, {"tool": "rf_towers", "ok": True}]
        record = harness.score(CASE, "mcp", trace, self._parsed("About 57%."), tmp_path)
        assert not record["passed"]
        assert record["forbidden_called"] == ["rf_towers"]

    def test_without_the_server_only_the_answer_is_scored(self, tmp_path):
        record = harness.score(CASE, "builtin", [], self._parsed("57%", ["Bash"]), tmp_path)
        assert record["tools_ok"] is None
        assert record["passed"]
        assert record["used_shell"]

    def test_the_harness_account_is_used_when_the_server_cannot_trace(self, tmp_path):
        parsed = harness.Parsed(final_text="57%", mcp_tools=["rf_link_budget"])
        record = harness.score(CASE, "mcp", [], parsed, tmp_path)
        assert record["called_tools"] == ["rf_link_budget"]

    def test_an_upstream_outage_is_not_counted_as_a_defect(self, tmp_path):
        trace = [{"tool": "rf_link_budget", "ok": False, "error": "Open-Meteo rate-limited this"}]
        record = harness.score(CASE, "mcp", trace, self._parsed("I could not finish."), tmp_path)
        assert record["upstream_outage"]

    def test_a_harness_error_fails_the_run(self, tmp_path):
        parsed = harness.Parsed(final_text="57%", error="timed out after 600s")
        trace = [{"tool": "rf_link_budget", "ok": True}]
        assert not harness.score(CASE, "mcp", trace, parsed, tmp_path)["passed"]


class TestSummary:
    def test_pass_every_repeat_counts_cases_not_runs(self):
        records = [
            {
                "case_id": "a",
                "scored": True,
                "passed": True,
                "duration_s": 1.0,
                "used_shell": False,
            },
            {
                "case_id": "a",
                "scored": True,
                "passed": False,
                "duration_s": 1.0,
                "used_shell": False,
            },
            {
                "case_id": "b",
                "scored": True,
                "passed": True,
                "duration_s": 1.0,
                "used_shell": False,
            },
        ]
        summary = harness.summarise(records)
        assert summary["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
        assert summary["pass_all_repeats"] == 0.5
        assert summary["by_case"] == {"a": "1/2", "b": "1/1"}


class TestIsolation:
    def test_agents_work_outside_the_repository_by_default(self, tmp_path):
        """An agent inside the checkout imported the server's own code."""
        run = harness.RunPaths(tmp_path / "evidence", tmp_path / "scratch")
        run.create()
        assert run.work.is_relative_to(tmp_path / "scratch")
        assert run.home.is_relative_to(tmp_path / "scratch")
        assert harness.inside_git_repo(run.work) is None

    def test_a_directory_inside_a_repository_is_detected(self, tmp_path):
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        nested = tmp_path / "repo" / "evals" / "results" / "work"
        nested.mkdir(parents=True)
        assert harness.inside_git_repo(nested) == tmp_path / "repo"

    def test_a_no_server_run_that_reads_the_server_code_is_not_scored(self, tmp_path):
        parsed = harness.Parsed(
            final_text="57%",
            harness_tools=["bash"],
            harness_inputs=[
                '{"command": "uv run python -c \'from geospatial_mcp.geo import rf\'"}'
            ],
        )
        record = harness.score(CASE, "builtin", [], parsed, tmp_path)
        assert record["contaminated"]
        assert not record["scored"]

    def test_this_checkout_would_be_refused(self):
        here = Path(__file__).resolve().parent
        assert harness.inside_git_repo(here) is not None


class TestEnvironment:
    def test_only_whitelisted_variables_reach_the_harness(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "must-not-leak")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        run = harness.RunPaths(tmp_path / "run")
        run.create()
        env = harness._environment(harness.Codex(None, None, {}), run)
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "CLAUDE_CODE_SESSION_ID" not in env
        assert env["OPENAI_API_KEY"] == "sk-test"
        assert env["CODEX_API_KEY"] == "sk-test"
        assert env["HOME"] == str(run.home)

    def test_a_subscription_login_is_carried_into_each_run(self, tmp_path, monkeypatch):
        auth_home = tmp_path / "codex-auth"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text("{}")
        monkeypatch.setenv("CODEX_AUTH_HOME", str(auth_home))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test")
        run = harness.RunPaths(tmp_path / "run")
        run.create()
        env = harness._environment(harness.Codex(None, None, {}), run)
        assert (run.home / ".codex" / "auth.json").is_file()
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-test"

    def test_claude_code_prompt_is_not_swallowed_by_a_variadic_flag(self, tmp_path):
        run = harness.RunPaths(tmp_path / "run")
        run.create()
        argv = harness.ClaudeCode("claude", None, {}).command(
            CASE, run, "mcp", {"command": "uv", "args": [], "env": {}}
        )
        assert argv[1:3] == ["-p", CASE["prompt"]]
        assert argv.index("--allowedTools") > argv.index("-p")

    def test_codex_refuses_the_none_condition(self):
        assert "none" not in harness.Codex.conditions

    def test_codex_final_message_path_survives_its_change_of_directory(self, tmp_path, monkeypatch):
        """Codex resolves -o against -C, so a relative --out wrote nowhere."""
        monkeypatch.chdir(tmp_path)
        run = harness.RunPaths(Path("results") / "case" / "r1", tmp_path / "scratch")
        run.create()
        argv = harness.Codex("codex", None, {}).command(
            CASE, run, "mcp", {"command": "uv", "args": [], "env": {}}
        )
        final = Path(argv[argv.index("-o") + 1])
        assert final.is_absolute()
        assert final.resolve() == (tmp_path / "results" / "case" / "r1" / "final.txt").resolve()
        assert argv[-1] == CASE["prompt"]
        assert "required" not in (run.home / ".codex" / "config.toml").read_text()

    def test_the_trace_path_survives_a_server_dir_elsewhere(self, tmp_path, monkeypatch):
        """`uv run --directory` moves the server, so the trace path must be absolute."""
        monkeypatch.chdir(tmp_path)
        run = harness.RunPaths(Path("results") / "case" / "r1")
        server = harness._server(CASE, run, tmp_path / "other-checkout")
        trace = Path(server["env"]["GEO_TRACE_FILE"])
        assert trace.is_absolute()
        assert trace.resolve() == (tmp_path / "results" / "case" / "r1" / "trace.jsonl").resolve()

    def test_the_server_is_started_by_module_not_by_command_name(self, tmp_path):
        """The console script is geospatial-mcp here and mcp-geospatial on main."""
        server = harness._server(CASE, harness.RunPaths(tmp_path / "run"), tmp_path)
        assert server["command"] == "uv"
        assert server["args"][-3:] == ["python", "-m", "geospatial_mcp"]
