"""The cross-harness runner: transcript parsing, scoring and environment hygiene.

No harness is launched here. The parsers are exercised on recorded transcripts
in ``fixtures/``, and scoring on hand-built traces, so these stay offline.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
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

    def test_opencode_config_path_survives_its_change_of_directory(self, tmp_path, monkeypatch):
        """opencode resolves OPENCODE_CONFIG against its own working directory: with a relative
        --out it found no config, so no server (every opencode run in September 2026)."""
        monkeypatch.chdir(tmp_path)
        run = harness.RunPaths(Path("results") / "case" / "r1", tmp_path / "scratch")
        run.create()
        adapter = harness.OpenCode("opencode", None, {})
        adapter.command(CASE, run, "mcp", {"command": "uv", "args": [], "env": {}})
        config = Path(adapter.env(run)["OPENCODE_CONFIG"])
        assert config.is_absolute()
        assert (
            config.resolve() == (tmp_path / "results" / "case" / "r1" / "opencode.json").resolve()
        )
        assert config.is_file()
        assert json.loads(config.read_text())["mcp"]["geospatial"]["timeout"] >= 30_000

    def test_opencode_does_not_read_claude_code_files(self, tmp_path):
        env = harness.OpenCode("opencode", None, {}).env(harness.RunPaths(tmp_path / "run"))
        assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"

    def test_the_server_is_started_by_module_not_by_command_name(self, tmp_path):
        """The console script was renamed from geospatial-mcp; the module name was not."""
        server = harness._server(CASE, harness.RunPaths(tmp_path / "run"), tmp_path)
        assert server["command"] == "uv"
        assert server["args"][-3:] == ["python", "-m", "geospatial_mcp"]


class _WhereAmI(harness.Adapter):
    """A stand-in harness that reports where it was started, and with what HOME."""

    name = "where-am-i"

    def command(self, case, run, condition, server):
        script = (
            "import json, os; print(json.dumps({'cwd': os.getcwd(), 'home': os.environ['HOME']}))"
        )
        return [sys.executable, "-c", script]

    def parse(self, run):
        return harness.Parsed(final_text=run.transcript.read_text(encoding="utf-8").strip())


class TestRelativePaths:
    """A relative --out sent opencode to look for its config under the run's work dir, so in
    September 2026 no opencode run had the server. Every path is made absolute up front."""

    def test_every_path_argument_is_made_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        argv = ["--harness", "opencode", "--out", "results/oc", "--work-root", "runs"]
        argv += ["--server-dir", ".", "--instructions", "AGENTS.md", "--bin", "bin/opencode"]
        args = harness.build_parser().parse_args(argv)
        harness.make_paths_absolute(args)
        here = tmp_path.resolve()
        assert args.out == here / "results" / "oc"
        assert args.work_root == here / "runs"
        assert args.server_dir == here
        assert args.instructions == here / "AGENTS.md"
        assert args.bin == str(here / "bin" / "opencode")

    def test_a_bare_harness_name_is_left_for_path_to_find(self):
        args = harness.build_parser().parse_args(["--harness", "opencode", "--bin", "opencode"])
        harness.make_paths_absolute(args)
        assert args.bin == "opencode"

    def test_a_relative_out_reaches_the_harness_as_an_absolute_path(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(harness.ADAPTERS, _WhereAmI.name, _WhereAmI)
        argv = ["harness.py", "--harness", _WhereAmI.name, "--condition", "builtin"]
        argv += ["--prompt", "Where are you?", "--out", "results/x", "--work-root", "runs"]
        monkeypatch.setattr(sys, "argv", argv)

        assert harness.main() == 0

        out = tmp_path.resolve() / "results" / "x"
        record = json.loads((out / "results.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert Path(record["run_dir"]).is_absolute()
        assert Path(record["run_dir"]).is_relative_to(out)
        assert Path(json.loads(record["final_text"])["home"]).is_absolute()
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["args"]["out"] == str(out)

    def test_run_case_makes_relative_paths_absolute_itself(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = harness.run_case(
            _WhereAmI(None, None, {}),
            CASE,
            "builtin",
            1,
            Path("results"),
            tmp_path,
            30.0,
            Path("runs"),
        )
        assert Path(record["run_dir"]).is_absolute()
        assert Path(json.loads(record["final_text"])["home"]).is_absolute()


class _Hangs(harness.Adapter):
    """A stand-in harness that never finishes."""

    name = "hangs"

    def command(self, case, run, condition, server):
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    def parse(self, run):
        return harness.Parsed()


class TestStopping:
    """Each run is its own process group, stopped as a whole: but not every OS has groups."""

    @pytest.mark.parametrize("process_groups", [True, False])
    def test_a_run_that_hangs_is_stopped_at_its_timeout(
        self, tmp_path, monkeypatch, process_groups
    ):
        if not process_groups:  # as on Windows, where CI runs these tests too
            monkeypatch.delattr(harness.os, "killpg", raising=False)
        elif not hasattr(harness.os, "killpg"):
            pytest.skip("this OS has no process groups")
        started = time.monotonic()
        record = harness.run_case(
            _Hangs(None, None, {}),
            CASE,
            "builtin",
            1,
            tmp_path / "out",
            tmp_path,
            0.5,
            tmp_path / "w",
        )
        assert record["error"].startswith("timed out")
        assert not record["passed"]
        assert time.monotonic() - started < 30


class TestWorkRoot:
    """Runs can be moved out of the home folder, whose .claude/ Claude Code reads.

    All 108 Claude Code runs recorded in September worked under
    ~/.cache/geospatial-evals and listed the operator's own skills and agents.
    """

    def test_the_default_has_a_neutral_name(self, monkeypatch):
        """It was ~/.cache/geospatial-evals until October 2026."""
        monkeypatch.delenv(harness.WORK_ROOT_ENV, raising=False)
        args = harness.build_parser().parse_args(["--harness", "codex"])
        assert args.work_root == Path.home() / ".cache" / "agent-runs"

    def test_the_environment_variable_moves_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv(harness.WORK_ROOT_ENV, str(tmp_path / "shared"))
        args = harness.build_parser().parse_args(["--harness", "codex"])
        assert args.work_root == tmp_path / "shared"

    def test_the_flag_wins_over_the_variable(self, monkeypatch, tmp_path):
        monkeypatch.setenv(harness.WORK_ROOT_ENV, str(tmp_path / "from-env"))
        args = harness.build_parser().parse_args(["--work-root", str(tmp_path / "from-flag")])
        assert args.work_root == tmp_path / "from-flag"

    def test_the_old_scratch_flag_still_works(self, tmp_path):
        args = harness.build_parser().parse_args(["--scratch", str(tmp_path / "old")])
        assert args.work_root == tmp_path / "old"

    def test_the_agent_starts_inside_the_work_root(self, tmp_path):
        work_root = tmp_path / "shared" / "geospatial-evals"
        record = harness.run_case(
            _WhereAmI(None, None, {}),
            CASE,
            "builtin",
            1,
            tmp_path / "results",
            tmp_path,
            30.0,
            work_root,
        )
        seen = json.loads(record["final_text"])
        assert Path(seen["cwd"]).resolve().is_relative_to(work_root.resolve())
        assert Path(seen["home"]).resolve().is_relative_to(work_root.resolve())
        # The evidence stays with the results; the scratch directory is removed.
        assert Path(record["run_dir"]).is_relative_to(tmp_path / "results")
        assert list(work_root.iterdir()) == []

    def test_agent_configuration_above_the_work_root_is_found(self, tmp_path):
        home = tmp_path / "home"
        (home / ".claude" / "agents").mkdir(parents=True)
        (home / "projects").mkdir()
        (home / "projects" / "AGENTS.md").write_text("be brief")
        work_root = home / "projects" / ".cache" / "geospatial-evals"

        found = harness.inherited_agent_config(work_root)

        assert home / ".claude" in found
        assert home / "projects" / "AGENTS.md" in found

    def test_only_directories_above_the_work_root_count(self, tmp_path):
        """A home folder beside the work root, as ~ is beside /Users/Shared, is not inherited."""
        (tmp_path / "Users" / "someone" / ".claude").mkdir(parents=True)
        (tmp_path / "Users" / "Shared").mkdir()
        work_root = tmp_path / "Users" / "Shared" / "geospatial-evals"
        found = harness.inherited_agent_config(work_root)
        assert [p for p in found if p.is_relative_to(tmp_path)] == []

    def test_a_work_root_that_inherits_configuration_is_warned_about(self, tmp_path, capsys):
        (tmp_path / ".claude").mkdir()
        harness.warn_about_inherited_config(tmp_path / "geospatial-evals")
        err = capsys.readouterr().err
        assert str(tmp_path / ".claude") in err
        assert "--work-root" in err
        assert harness.WORK_ROOT_ENV in err


class TestWorkRootName:
    """The model sees its working directory, and the folder's name can hint at the tools.

    On telecom-cell-area-sum-007 with Sonnet 5.5 (October 2026), runs under
    /Users/Shared/geospatial-evals called rf_link_budget in 6 of 11 runs, and
    runs under neutral folders in 0 of 20.
    """

    @pytest.mark.parametrize(
        ("path", "words"),
        [
            ("/Users/Shared/geospatial-evals", ["geospatial", "evals"]),
            ("/Users/Shared/GeoEvals", ["geo", "evals"]),
            ("/srv/MCPServer/runs", ["mcp"]),
            ("/Users/Shared/tool_tests", ["tool"]),
            ("/Users/Shared/rf-runs", ["rf"]),  # a prefix of the server's tool names
        ],
    )
    def test_a_hinting_path_is_flagged(self, path, words):
        assert harness.work_root_hint_words(Path(path)) == words

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/Shared/agent-runs",
            "/srv/medieval/george",  # only whole words count
            "/data/runs",  # data_convert's prefix, but an ordinary folder name
        ],
    )
    def test_a_neutral_path_is_not_flagged(self, path):
        assert harness.work_root_hint_words(Path(path)) == []

    def test_the_old_default_carried_the_hint(self):
        old = Path.home() / ".cache" / "geospatial-evals"
        assert "geospatial" in harness.work_root_hint_words(old)

    def test_the_recommended_example_is_neutral(self):
        assert harness.work_root_hint_words(Path(harness.NEUTRAL_WORK_ROOT_EXAMPLE)) == []

    def test_every_tool_domain_in_the_library_is_a_hint(self):
        """Kept in step with the tools the cases name, bar the two ordinary folder names."""
        named = {
            tool
            for case in harness.load_cases(None, include_network=True)
            for key in ("expect_tools", "optional_tools", "forbid_tools")
            for tool in case.get(key, [])
        }
        domains = {tool.split("_")[0] for tool in named} - {"data", "env"}
        assert domains <= set(harness.TOOL_PREFIXES)

    def test_a_hinting_work_root_is_warned_about(self, capsys):
        words = harness.warn_about_hint_words(Path("/Users/Shared/geospatial-evals"))
        err = capsys.readouterr().err
        assert words == ["geospatial", "evals"]
        assert '"geospatial", "evals"' in err
        assert "--work-root" in err
        assert harness.WORK_ROOT_ENV in err
        assert harness.NEUTRAL_WORK_ROOT_EXAMPLE in err
        # It points at the neutral default as the way out.
        assert str(harness.DEFAULT_WORK_ROOT) in err

    def test_the_default_is_not_warned_about(self, capsys):
        # Under a neutral home folder: the real one's path is not this test's to choose.
        default = Path("/Users/someone") / harness.DEFAULT_WORK_ROOT.relative_to(Path.home())
        assert harness.warn_about_hint_words(default) == []
        assert capsys.readouterr().err == ""

    def test_a_neutral_work_root_is_not_warned_about(self, capsys):
        assert harness.warn_about_hint_words(Path("/Users/Shared/agent-runs")) == []
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize(
        ("folder", "hinted"), [("geospatial-evals", True), ("agent-runs", False)]
    )
    def test_summary_records_the_words(self, tmp_path, monkeypatch, capsys, folder, hinted):
        """A reader of the results can tell whether the runs' path carried a hint."""
        monkeypatch.setitem(harness.ADAPTERS, _WhereAmI.name, _WhereAmI)
        work_root = tmp_path / folder
        out = tmp_path / "results"
        argv = ["harness.py", "--harness", _WhereAmI.name, "--condition", "builtin"]
        argv += ["--prompt", "Where are you?", "--out", str(out), "--work-root", str(work_root)]
        monkeypatch.setattr(sys, "argv", argv)

        assert harness.main() == 0

        recorded = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        words = recorded["work_root_hint_words"]
        # Compared with the function, not with [], so a temporary folder whose own
        # path happens to hold a hint word does not fail the neutral case.
        assert words == harness.work_root_hint_words(work_root)
        assert ("geospatial" in words) is hinted
        assert ("could tell the model" in capsys.readouterr().err) is bool(words)
