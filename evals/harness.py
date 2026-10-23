"""Run the eval library inside real agent harnesses, not a bare API loop.

``run.py --mode agent`` drives one model through the raw API with nothing but
this server's tools. People run a harness -- Claude Code, Codex, opencode --
with its own system prompt and its own tools, including a shell the model can
use to do the maths itself. Whether the model reaches for this server in *that*
setting, and gets the answer right, is the question that matters, and only
running the harness answers it.

Every run gets a fresh working directory and HOME, and a whitelisted
environment: only the variables in ``PASSTHROUGH`` and ``PROVIDER_KEYS``
reach the harness, so no ambient credential in the calling shell is used by
accident. Both directories are made under the work root (``--work-root``,
default ~/.cache/agent-runs). Keep it outside your home folder, because Claude
Code also reads the .claude/ folder and CLAUDE.md of every directory above its
working directory. And give it a name that says nothing about the tools,
because Claude Code shows the model its working directory: on one case, a
folder named geospatial-evals changed whether the model used them (see
:data:`HINT_WORDS`).

Tool calls come from the server's own trace (``GEO_TRACE_FILE``), identical
for every harness; the transcript supplies the final answer, the harness's
own tool use (shell, web) and cost.

    uv run python evals/harness.py --harness opencode --model opencode/<model>
    uv run python evals/harness.py --harness claude-code --condition builtin --filter demo
    uv run python evals/harness.py --harness codex --model <model> --repeat 3
    uv run python evals/harness.py --harness claude-code --work-root /Users/Shared/agent-runs
    uv run python evals/harness.py --report evals/results/harness-*

Conditions:
  mcp       the harness plus this server -- what people actually run
  mcp-only  this server's tools and nothing else: isolates tool descriptions
  builtin   the harness alone: its shell, file and web tools, no server
  none      no tools at all, the bare model

Codex cannot switch its shell off, so it runs mcp and builtin only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))

from answers import check_answer, check_artifacts  # noqa: E402
from run import UPSTREAM_FAILURE_MARKERS, load_cases  # noqa: E402

RESULTS_DIR = HERE / "results"

#: Plumbing a harness needs to reach the network through this machine's proxy.
PASSTHROUGH = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "UV_CACHE_DIR",
)

#: Model-provider keys, passed only when set. Nothing else -- in particular no
#: cloud credentials -- is forwarded, so a run can only spend what it was given.
PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",  # a Claude subscription, from `claude setup-token`
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "CO_API_KEY",
    "COHERE_API_KEY",
)

MCP_NAME = "geospatial"

#: Conditions in which the server is attached, so tool selection is scored.
WITH_SERVER = ("mcp", "mcp-only")

#: Strings that show an agent reaching into this server's own code or checkout.
SERVER_MARKERS = ("geospatial_mcp", "geospatial-mcp", "mcp-geospatial")

#: Where each run's working directory and HOME are made, unless --work-root or
#: GEO_EVALS_WORK_ROOT says otherwise. Each run works in a fresh run-XXXXXXXX/work
#: below it. Not under /tmp: Codex refuses to install its helper binaries there.
#:
#: The name says nothing about the server, because the model sees its working
#: directory. Until October 2026 the default was ~/.cache/geospatial-evals, and
#: on telecom-cell-area-sum-007 (Sonnet 5.5 in Claude Code) runs under a folder
#: named geospatial-evals called rf_link_budget in 6 of 11 runs, and runs under
#: neutral folders in 0 of 20 (see :data:`HINT_WORDS`). A runner whose default
#: hints at the tools measures the hint. To compare with runs recorded under the
#: old folder, pass --work-root ~/.cache/geospatial-evals.
#:
#: It is still inside the home folder, which Claude Code reads from below (see
#: :func:`inherited_agent_config`), so the harness warns when ~/.claude exists;
#: a work root outside it, such as /Users/Shared/agent-runs, avoids that too.
DEFAULT_WORK_ROOT = Path.home() / ".cache" / "agent-runs"

#: The variable that moves the work root without a command-line flag.
WORK_ROOT_ENV = "GEO_EVALS_WORK_ROOT"

#: Agent configuration a harness may pick up from a directory above its own.
#: Claude Code reads the CLAUDE.md and the .claude/ folder (skills, agents,
#: commands) of every parent of its working directory, whatever HOME is set to.
AGENT_CONFIG = (
    ".claude",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    ".opencode",
    "opencode.json",
)

#: Words in the work root's path that could tell the model what is being tested.
#: Claude Code shows the model its working directory. On one case
#: (telecom-cell-area-sum-007, claude-sonnet-5-5 at medium effort, October 2026)
#: runs under /Users/Shared/geospatial-evals called rf_link_budget and passed in
#: 6 of 11 runs, and runs under neutral folders in 0 of 20. The word that differed
#: was "geospatial": two of the neutral folders were named evals-neutral and
#: eval-scratch. The other words are flagged as a precaution.
HINT_WORDS = (
    "geospatial",
    "spatial",
    "gis",
    "geo",
    "mcp",
    "eval",
    "evals",
    "evaluation",
    "evaluations",
    "tool",
    "tools",
)

#: The domains this server's tool names start with, as in rf_link_budget (the
#: naming convention in tests/mcp/test_contract.py). A folder called rf-runs
#: hints as much as one called geospatial. "data" and "env" are left out: they
#: name ordinary folders (/data, a virtualenv's env), and a warning that fires
#: on those would be ignored.
TOOL_PREFIXES = (
    "geo",
    "coord",
    "geom",
    "place",
    "route",
    "terrain",
    "sun",
    "time",
    "rf",
    "orbit",
    "hazard",
    "track",
    "stats",
    "raster",
)

#: A work root with none of those words in it.
NEUTRAL_WORK_ROOT_EXAMPLE = "/Users/Shared/agent-runs"


# ---------------------------------------------------------------- run layout
@dataclass
class RunPaths:
    """Evidence lives under ``root``; the agent works under ``scratch``, a fresh
    directory in the work root.

    The two are kept apart because an agent explores its working directory.
    Our first no-server runs did so from inside this repository: the harness
    took the whole checkout as its project, and the model imported the
    server's own grid-reference code instead of doing the work.
    """

    root: Path
    scratch: Path | None = None

    @property
    def work(self) -> Path:
        return (self.scratch or self.root) / "work"

    @property
    def home(self) -> Path:
        return (self.scratch or self.root) / "home"

    @property
    def trace(self) -> Path:
        return self.root / "trace.jsonl"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.jsonl"

    @property
    def stderr(self) -> Path:
        return self.root / "stderr.txt"

    @property
    def final(self) -> Path:
        return self.root / "final.txt"

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.work, self.home):
            path.mkdir(parents=True, exist_ok=True)


def default_work_root(environ: Mapping[str, str] | None = None) -> Path:
    """``GEO_EVALS_WORK_ROOT`` if it is set, otherwise :data:`DEFAULT_WORK_ROOT`."""
    value = (os.environ if environ is None else environ).get(WORK_ROOT_ENV, "").strip()
    return Path(value).expanduser() if value else DEFAULT_WORK_ROOT


def inherited_agent_config(work_root: Path) -> list[Path]:
    """Agent configuration in ``work_root`` or above it, which a run would inherit.

    Every run's fresh HOME hides the user-level configuration, but not this.
    All 108 Claude Code runs recorded in September 2026 worked under
    ~/.cache/geospatial-evals, and every one listed the operator's personal
    skills and agents from ~/.claude in its init event. A work root outside
    the home folder, such as /Users/Shared/agent-runs, has none above it.
    """
    start = work_root.expanduser().absolute()
    return [
        parent / name
        for parent in (start, *start.parents)
        for name in AGENT_CONFIG
        if (parent / name).exists()
    ]


def warn_about_inherited_config(work_root: Path) -> list[Path]:
    """Say on stderr when runs under ``work_root`` would inherit agent configuration."""
    found = inherited_agent_config(work_root)
    if found:
        listed = "\n".join(f"  {path}" for path in found)
        print(
            f"warning: runs under {work_root} can see agent configuration above them:\n"
            f"{listed}\n"
            "Claude Code loads the skills, agents and CLAUDE.md it finds there into every run. "
            f"Use --work-root or {WORK_ROOT_ENV} to work outside the home folder, "
            f"e.g. {NEUTRAL_WORK_ROOT_EXAMPLE}.",
            file=sys.stderr,
        )
    return found


def _words(component: str) -> list[str]:
    """Split one path component into lower-case words: geospatial-evals and
    GeoEvals both give two, MCPServer gives mcp and server."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", component)
    return [word for word in re.split(r"[^a-z0-9]+", spaced.lower()) if word]


def work_root_hint_words(work_root: Path) -> list[str]:
    """Words in ``work_root``'s path that could hint at the tools being tested.

    Only whole words count, so a folder named george or medieval is not
    flagged. Below the work root the harness adds only run-<random>/work.
    """
    hints = {*HINT_WORDS, *TOOL_PREFIXES}
    path = work_root.expanduser().absolute()
    found = (word for part in path.parts for word in _words(part) if word in hints)
    return list(dict.fromkeys(found))


def warn_about_hint_words(work_root: Path) -> list[str]:
    """Say on stderr when the work root's name could hint at the tools being tested."""
    words = work_root_hint_words(work_root)
    if words:
        listed = ", ".join(f'"{word}"' for word in words)
        print(
            f"warning: the work root {work_root} contains {listed}, which could tell the model "
            "what is being tested.\n"
            "Claude Code shows the model its working directory. On one case with Sonnet 5.5 "
            "(October 2026), runs under /Users/Shared/geospatial-evals used the expected tool "
            "in 6 of 11 runs, and runs under neutral folders in 0 of 20.\n"
            f"To measure without the hint, leave out --work-root and {WORK_ROOT_ENV} for the "
            f"neutral default, {DEFAULT_WORK_ROOT}, or give a neutral name, "
            f"e.g. {NEUTRAL_WORK_ROOT_EXAMPLE}.",
            file=sys.stderr,
        )
    return words


def inside_git_repo(path: Path) -> Path | None:
    """The repository ``path`` is inside, if any -- where an agent would roam."""
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


@dataclass
class Parsed:
    """What a harness transcript says, independent of the harness."""

    final_text: str = ""
    harness_tools: list[str] = field(default_factory=list)  # its own tools: shell, web...
    harness_inputs: list[str] = field(default_factory=list)  # what it ran them with
    mcp_tools: list[str] = field(default_factory=list)  # as the harness saw them
    cost_usd: float | None = None
    tokens: int | None = None
    turns: int | None = None
    error: str = ""
    server_connected: bool | None = None


# ------------------------------------------------------------------ adapters
class Adapter:
    name = ""
    binary = ""
    conditions: tuple[str, ...] = ("mcp", "mcp-only", "builtin", "none")

    def __init__(self, binary: str | None, model: str | None, extra: dict[str, Any]):
        self.bin = binary or shutil.which(self.binary) or self.binary
        self.model = model
        self.extra = extra

    def command(
        self, case: dict[str, Any], run: RunPaths, condition: str, server: dict[str, Any]
    ) -> list[str]:
        raise NotImplementedError

    def parse(self, run: RunPaths) -> Parsed:
        raise NotImplementedError

    def env(self, run: RunPaths) -> dict[str, str]:
        """Harness-specific variables, on top of the whitelist."""
        return {}

    def preflight(self, run: RunPaths, condition: str, env: dict[str, str]) -> bool | None:
        """Whether the server connects before the run, for harnesses whose transcript doesn't say."""
        return None


class ClaudeCode(Adapter):
    name = "claude-code"
    binary = "claude"

    BUILTIN = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch")

    def command(self, case, run, condition, server):
        # The prompt goes straight after -p: a variadic option such as
        # --allowedTools would otherwise swallow it.
        argv = [self.bin, "-p", case["prompt"], "--output-format", "stream-json", "--verbose"]
        # Not --bare: it ignores CLAUDE_CODE_OAUTH_TOKEN and skips CLAUDE.md, which
        # --instructions relies on. The fresh HOME hides the user-level settings,
        # but not the .claude/ and CLAUDE.md of the directories above the working
        # directory: that takes a work root outside the home folder (--work-root).
        argv += ["--no-session-persistence", "--strict-mcp-config"]
        argv += ["--permission-mode", "dontAsk", "--max-turns", str(_turns(case))]
        argv += ["--max-budget-usd", str(self.extra.get("max_budget_usd", 1.0))]
        # --tools "" leaves these in the catalogue, and Monitor runs shell commands.
        argv += ["--disallowedTools", "Monitor", "PushNotification", "RemoteTrigger"]
        if self.model:
            argv += ["--model", self.model]
        if condition == "none":
            return [*argv, "--tools", ""]
        builtin = condition != "mcp-only"
        allowed = list(self.BUILTIN) if builtin else []
        if condition in WITH_SERVER:
            config = {"mcpServers": {MCP_NAME: server}}
            argv += ["--mcp-config", json.dumps(config)]
            allowed.append(f"mcp__{MCP_NAME}")
        return [*argv, "--tools", "default" if builtin else "", "--allowedTools", *allowed]

    def env(self, run):
        # Without --bare, a run with no key could fall back to this machine's login.
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
            raise SystemExit(
                "set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY for Claude Code runs"
            )
        return {}

    def parse(self, run):
        parsed = Parsed()
        for event in _jsonl(run.transcript):
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                servers = {s.get("name"): s.get("status") for s in event.get("mcp_servers", [])}
                if MCP_NAME in servers:
                    parsed.server_connected = servers[MCP_NAME] == "connected"
            elif kind == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        prefix = f"mcp__{MCP_NAME}__"
                        if name.startswith(prefix):
                            parsed.mcp_tools.append(name[len(prefix) :])
                        else:
                            parsed.harness_tools.append(name)
                            parsed.harness_inputs.append(json.dumps(block.get("input")))
            elif kind == "result":
                parsed.final_text = event.get("result") or ""
                parsed.cost_usd = event.get("total_cost_usd")
                parsed.turns = event.get("num_turns")
                if event.get("is_error"):
                    parsed.error = str(event.get("subtype") or "error")
        if not parsed.final_text and not parsed.error:
            parsed.error = "no result event"
        return parsed


class Codex(Adapter):
    name = "codex"
    binary = "codex"
    conditions = ("mcp", "builtin")  # its shell cannot be switched off

    def command(self, case, run, condition, server):
        home = run.home / ".codex"
        home.mkdir(parents=True, exist_ok=True)
        lines = [
            'approval_policy = "never"',
            'sandbox_mode = "workspace-write"',
            "",
            "[sandbox_workspace_write]",
            "network_access = true  # parity with the other harnesses' shells",
        ]
        if condition == "mcp":
            lines += [
                "",
                f"[mcp_servers.{MCP_NAME}]",
                f"command = {json.dumps(server['command'])}",
                f"args = {json.dumps(server['args'])}",
                "env = { "
                + ", ".join(f"{k} = {json.dumps(v)}" for k, v in server["env"].items())
                + " }",
                "startup_timeout_sec = 60",
                "tool_timeout_sec = 180",
            ]
        (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        argv = [self.bin, "exec", "--json", "--skip-git-repo-check", "--ephemeral"]
        # Codex resolves -o against -C, and run-batch.sh passes a relative --out,
        # so a relative path here sent "Failed to write last message file".
        argv += ["-C", str(run.work), "-o", str(run.final.absolute())]
        if self.extra.get("codex_bypass_sandbox"):
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        if self.model:
            argv += ["-m", self.model]
        return [*argv, case["prompt"]]

    def env(self, run: RunPaths) -> dict[str, str]:
        # A ChatGPT sign-in (`codex login --device-auth`) lives in auth.json; copy it
        # into the run's fresh CODEX_HOME. CODEX_AUTH_HOME says where to find it.
        home = run.home / ".codex"
        source = Path(os.environ.get("CODEX_AUTH_HOME") or Path.home() / ".codex") / "auth.json"
        if source.is_file():
            home.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, home / "auth.json")
        extra = {"CODEX_HOME": str(home)}
        key = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if key:
            extra["CODEX_API_KEY"] = key
        return extra

    def parse(self, run):
        parsed = Parsed()
        messages: list[str] = []
        tokens = 0
        turns = 0
        for event in _jsonl(run.transcript):
            kind = event.get("type", "")
            if kind == "item.completed":
                item = event.get("item", {})
                item_type = item.get("type", "")
                if item_type == "agent_message":
                    messages.append(item.get("text", ""))
                elif item_type == "mcp_tool_call":
                    if item.get("server") == MCP_NAME:
                        parsed.mcp_tools.append(item.get("tool", ""))
                elif item_type in {"command_execution", "file_change", "web_search"}:
                    parsed.harness_tools.append(item_type)
                    parsed.harness_inputs.append(json.dumps(item))
            elif kind == "turn.completed":
                turns += 1
                usage = event.get("usage", {})
                tokens += int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
            elif kind in {"turn.failed", "error"}:
                parsed.error = json.dumps(event.get("error") or event)[:300]
        final = run.final.read_text(encoding="utf-8") if run.final.is_file() else ""
        parsed.final_text = final or (messages[-1] if messages else "")
        parsed.tokens = tokens or None
        parsed.turns = turns or None
        if not parsed.final_text and not parsed.error:
            parsed.error = "no final message"
        return parsed


class OpenCode(Adapter):
    name = "opencode"
    binary = "opencode"

    MCP_STARTUP_TIMEOUT_MS = 30_000

    BUILTIN = (
        "bash",
        "edit",
        "write",
        "read",
        "grep",
        "glob",
        "list",
        "patch",
        "todowrite",
        "todoread",
        "webfetch",
        "websearch",
        "codesearch",
        "task",
        "skill",
    )

    def command(self, case, run, condition, server):
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
        }
        if condition in WITH_SERVER:
            config["mcp"] = {
                MCP_NAME: {
                    "type": "local",
                    "command": [server["command"], *server["args"]],
                    "environment": server["env"],
                    "enabled": True,
                    # How long opencode waits for the server's tool list, in ms (its default is
                    # 5000). A cold `uv run` can take longer than that before the server answers.
                    "timeout": self.MCP_STARTUP_TIMEOUT_MS,
                }
            }
        # Switch the built-in tools off in both places. opencode turns "tools" into permission rules,
        # and an explicit "allow" above outranks them: with only {"*": False}, a "none" run still used
        # bash and webfetch (opencode 1.19.3, October 2026).
        if condition == "none":
            config["tools"] = {"*": False}
            config["permission"] = {"*": "deny"}
        elif condition == "mcp-only":
            config["tools"] = dict.fromkeys(self.BUILTIN, False)
            config["permission"] = dict.fromkeys(self.BUILTIN, "deny")
        # Outside the working directory, so the model never sees its own config.
        (run.root / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        argv = [self.bin, "run", case["prompt"], "--format", "json"]
        if self.model:
            argv += ["-m", self.model]
        return argv

    def env(self, run: RunPaths) -> dict[str, str]:
        return {
            # Absolute. opencode resolves a relative OPENCODE_CONFIG against its own working
            # directory, the run's work dir, where there is no such file, and then runs with no
            # config at all and no warning. In September 2026 run-batch.sh passed a relative --out,
            # so every opencode run had no server ("No MCP servers configured"; opencode 1.18.33
            # and 1.19.3 both do this). The stage passed an absolute --out and was unaffected.
            "OPENCODE_CONFIG": str((run.root / "opencode.json").absolute()),
            # opencode also reads Claude Code's files: ~/.claude/CLAUDE.md and the skills in every
            # .claude/ folder above the working directory, which the default work root has.
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            # No self-update in the middle of a batch.
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }

    def preflight(self, run: RunPaths, condition: str, env: dict[str, str]) -> bool | None:
        """Ask opencode, with the run's own config and environment, whether the server connects.

        `opencode run` says nothing when a configured server is missing or fails to start, so
        without this a run with no server looks like a run whose model ignored it. The output
        is kept as mcp-list.txt.
        """
        if condition not in WITH_SERVER:
            return None
        try:
            done = subprocess.run(
                [self.bin, "mcp", "list"],
                cwd=run.work,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.MCP_STARTUP_TIMEOUT_MS / 1000 + 60,
                check=False,
            )
            text = done.stdout + done.stderr
        except subprocess.TimeoutExpired:
            text = "opencode mcp list timed out"
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        (run.root / "mcp-list.txt").write_text(text, encoding="utf-8")
        return any(
            f"✓ {MCP_NAME}" in line and "connected" in line and "disconnected" not in line
            for line in text.splitlines()
        )

    def parse(self, run):
        parsed = Parsed()
        texts_since_tool: list[str] = []
        all_texts: list[str] = []
        tokens = 0
        cost = 0.0
        turns = 0
        prefix = f"{MCP_NAME}_"
        for event in _jsonl(run.transcript):
            part = event.get("part", {})
            kind = event.get("type")
            if kind == "tool_use":
                name = part.get("tool", "")
                if name.startswith(prefix):
                    parsed.mcp_tools.append(name[len(prefix) :])
                else:
                    parsed.harness_tools.append(name)
                    parsed.harness_inputs.append(json.dumps(part.get("state", {}).get("input")))
                texts_since_tool = []
            elif kind == "text":
                texts_since_tool.append(part.get("text", ""))
                all_texts.append(part.get("text", ""))
            elif kind == "step_finish":
                turns += 1
                tokens += int(part.get("tokens", {}).get("total", 0))
                cost += float(part.get("cost") or 0)
            elif kind == "error":
                parsed.error = json.dumps(event.get("error") or event)[:300]
        parsed.final_text = "\n".join(texts_since_tool or all_texts)
        parsed.tokens = tokens or None
        parsed.cost_usd = cost
        parsed.turns = turns or None
        if not parsed.final_text and not parsed.error:
            parsed.error = "no text in transcript"
        return parsed


ADAPTERS: dict[str, type[Adapter]] = {a.name: a for a in (ClaudeCode, Codex, OpenCode)}


# -------------------------------------------------------------------- running
def _turns(case: dict[str, Any]) -> int:
    """Harness turns are finer-grained than API turns; allow headroom."""
    return max(12, 2 * int(case.get("max_turns", 6)))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _server(case: dict[str, Any], run: RunPaths, server_dir: Path) -> dict[str, Any]:
    toolsets = ",".join(case.get("toolsets", [])) or "default"
    cache = os.environ.get("GEO_CACHE_DIR") or str(Path.home() / ".cache" / "geospatial-mcp")
    return {
        "command": "uv",
        # The module, not the console script: main renamed the command to
        # mcp-geospatial, and the import name is the same on both sides.
        "args": ["run", "--directory", str(server_dir), "python", "-m", "geospatial_mcp"],
        "env": {
            # Absolute: `uv run --directory` starts the server in server_dir, so a
            # relative path only lands in the run directory when server_dir is
            # this checkout. With --server-dir elsewhere the trace went missing.
            "GEO_TRACE_FILE": str(run.trace.absolute()),
            "GEO_TOOLSETS": toolsets,
            # Shared across runs: repeated questions hit the cache, not the free APIs.
            "GEO_CACHE_DIR": cache,
        },
    }


def _environment(adapter: Adapter, run: RunPaths) -> dict[str, str]:
    env = {k: os.environ[k] for k in (*PASSTHROUGH, *PROVIDER_KEYS) if os.environ.get(k)}
    # `uv run` puts this checkout's .venv/bin first on PATH: the agent's python3
    # would then have the server's package and its geo libraries importable.
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(str(REPO_ROOT))
    )
    env["HOME"] = str(run.home)
    env.setdefault("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))
    env.update(adapter.env(run))
    return env


def _stop_group(proc: subprocess.Popen[Any]) -> None:
    """Stop a run's process group: whatever is still running after it finished, or all of it on
    a timeout. TERM first, then KILL."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # the whole group has already exited
    else:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        time.sleep(1)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def run_case(
    adapter: Adapter,
    case: dict[str, Any],
    condition: str,
    repeat: int,
    out: Path,
    server_dir: Path,
    timeout_s: float,
    work_root: Path,
    instructions: Path | None = None,
) -> dict[str, Any]:
    # Absolute even when called with relative paths (main makes them so already): every
    # path below reaches a harness that starts in another directory.
    root = (out / case["id"] / f"r{repeat}").absolute()
    if root.exists():
        shutil.rmtree(root)
    work_root = work_root.absolute()
    work_root.mkdir(parents=True, exist_ok=True)
    run = RunPaths(root, Path(tempfile.mkdtemp(prefix="run-", dir=work_root)))
    run.create()
    repo = inside_git_repo(run.work)
    if repo:
        raise SystemExit(f"refusing to run an agent inside the repository at {repo}")
    if instructions:
        # Project instructions, where each harness looks for them.
        for name in ("AGENTS.md", "CLAUDE.md"):
            shutil.copy(instructions, run.work / name)
    server = _server(case, run, server_dir)
    argv = adapter.command(case, run, condition, server)
    extra = adapter.extra.get("harness_args") or []
    if extra:
        # Before the prompt for Codex, whose prompt is the last positional.
        argv = [*argv[:-1], *extra, argv[-1]] if adapter.name == "codex" else [*argv, *extra]
    env = _environment(adapter, run)
    connected = adapter.preflight(run, condition, env)
    started = time.monotonic()
    timed_out = False
    with run.transcript.open("w") as stdout, run.stderr.open("w") as stderr:
        # In its own process group, so a hung run is stopped with everything it started (the
        # server, shells), not only the harness's own process.
        proc = subprocess.Popen(
            argv,
            cwd=run.work,
            env=env,
            stdin=subprocess.DEVNULL,  # an open stdin stalls some harnesses
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            duration = time.monotonic() - started
            _stop_group(proc)
    parsed = adapter.parse(run)
    if parsed.server_connected is None:
        parsed.server_connected = connected
    if timed_out:
        parsed.error = f"timed out after {timeout_s:.0f}s"
    run.final.write_text(parsed.final_text, encoding="utf-8")
    record = score(case, condition, _jsonl(run.trace), parsed, run.work)
    # Keep what the agent wrote as evidence; drop its HOME (caches and logs).
    shutil.copytree(run.work, run.root / "work", dirs_exist_ok=True)
    if run.scratch:
        shutil.rmtree(run.scratch, ignore_errors=True)
    record.update(
        harness=adapter.name,
        model=adapter.model,
        condition=condition,
        repeat=repeat,
        duration_s=round(duration, 1),
        cost_usd=parsed.cost_usd,
        tokens=parsed.tokens,
        turns=parsed.turns,
        harness_tools=parsed.harness_tools,
        server_connected=parsed.server_connected,
        error=parsed.error,
        final_text=parsed.final_text[:4000],
        run_dir=str(run.root),
    )
    return record


# -------------------------------------------------------------------- scoring
def score(
    case: dict[str, Any],
    condition: str,
    trace: list[dict[str, Any]],
    parsed: Parsed,
    workdir: Path,
) -> dict[str, Any]:
    # The server's trace is the record of what was called. A server too old to
    # trace falls back to the harness's own account.
    called = [c["tool"] for c in trace] if trace else list(parsed.mcp_tools)
    failures = [c for c in trace if not c.get("ok")]
    upstream = [
        c for c in failures if any(m in c.get("error", "") for m in UPSTREAM_FAILURE_MARKERS)
    ]
    record: dict[str, Any] = {
        "case_id": case["id"],
        "persona": case["persona"],
        "axis": case["axis"],
        "called_tools": called,
        "failed_calls": len(failures),
        "upstream_failures": len(upstream),
        "used_shell": any(t.lower() in {"bash", "command_execution"} for t in parsed.harness_tools),
        # Reaching into this server's code without the server is not doing the work.
        "contaminated": condition not in WITH_SERVER
        and any(marker in text for text in parsed.harness_inputs for marker in SERVER_MARKERS),
    }

    tools_ok: bool | None = None
    if condition in WITH_SERVER:
        expected = set(case.get("expect_tools", []))
        optional = set(case.get("optional_tools", []))
        forbidden = set(case.get("forbid_tools", []))
        called_set = set(called)
        hit = expected & called_set
        pool = called_set - optional
        precision = len(hit) / len(pool) if pool else 0.0
        recall = len(hit) / len(expected) if expected else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        tools_ok = expected <= called_set and not (called_set & forbidden)
        record.update(
            tool_selection_f1=round(f1, 4),
            first_call_correct=bool(called) and called[0] in expected | optional,
            forbidden_called=sorted(called_set & forbidden),
            missed_tools=sorted(expected - called_set),
        )

    answer_ok, answer_detail = check_answer(case.get("answer"), parsed.final_text)
    artifacts_ok, artifacts_detail = check_artifacts(case.get("artifacts"), workdir)
    checks = [tools_ok, answer_ok, artifacts_ok]
    if record["contaminated"]:
        checks = [None]  # not a fair attempt, so not scored either way
    record.update(
        tools_ok=tools_ok,
        answer_ok=answer_ok,
        answer_detail=answer_detail,
        artifacts_ok=artifacts_ok,
        artifacts_detail=artifacts_detail,
        scored=any(c is not None for c in checks),
        passed=all(c is not False for c in checks) and not parsed.error,
        upstream_outage=bool(upstream) and not all(c is not False for c in checks),
    )
    return record


# ------------------------------------------------------------------ reporting
def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in records if r.get("scored") and not r.get("upstream_outage")]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for r in scored:
        by_case.setdefault(r["case_id"], []).append(r)
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    mcp = [r for r in scored if r.get("tools_ok") is not None]
    answered = [r for r in scored if r.get("answer_ok") is not None]
    return {
        "runs": len(records),
        "scored_runs": len(scored),
        "pass_rate": _rate(r["passed"] for r in scored),
        # pass^k: the share of cases that passed on *every* repeat. One run is an anecdote.
        "pass_all_repeats": _rate(all(r["passed"] for r in group) for group in by_case.values()),
        "tool_selection_ok": _rate(r["tools_ok"] for r in mcp),
        "first_call_correct": _rate(r.get("first_call_correct") for r in mcp),
        "answer_ok": _rate(r["answer_ok"] for r in answered),
        "used_shell": _rate(r["used_shell"] for r in records),
        "harness_errors": sum(1 for r in records if r.get("error")),
        "contaminated_runs": sum(1 for r in records if r.get("contaminated")),
        "upstream_outages": sum(1 for r in records if r.get("upstream_outage")),
        "mean_duration_s": _mean(r["duration_s"] for r in records),
        "mean_cost_usd": _mean(costs) if costs else None,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "by_case": {
            case: f"{sum(r['passed'] for r in group)}/{len(group)}"
            for case, group in sorted(by_case.items())
        },
    }


def _rate(values: Any) -> float | None:
    items = [bool(v) for v in values if v is not None]
    return round(sum(items) / len(items), 3) if items else None


def _mean(values: Any) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 3) if items else None


def report(dirs: list[Path]) -> int:
    """Compare finished runs side by side, as a Markdown table."""
    rows = []
    for directory in dirs:
        summary_file = directory / "summary.json"
        if not summary_file.is_file():
            print(f"skipping {directory}: no summary.json", file=sys.stderr)
            continue
        data = json.loads(summary_file.read_text(encoding="utf-8"))
        rows.append((data["label"], data["summary"]))
    if not rows:
        return 2
    columns = [
        ("pass", "pass_rate"),
        ("pass every repeat", "pass_all_repeats"),
        ("right tools", "tool_selection_ok"),
        ("right answer", "answer_ok"),
        ("used shell", "used_shell"),
        ("mean s", "mean_duration_s"),
        ("mean $", "mean_cost_usd"),
    ]
    print("| run | " + " | ".join(title for title, _ in columns) + " |")
    print("|---|" + "---|" * len(columns))
    for label, summary in rows:
        cells = [_cell(summary.get(key)) for _, key in columns]
        print(f"| {label} | " + " | ".join(cells) + " |")
    return 0


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value <= 1.0:
        return f"{value:.0%}"
    return f"{value}"


# ----------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--harness", choices=sorted(ADAPTERS))
    parser.add_argument(
        "--condition", choices=("mcp", "mcp-only", "builtin", "none"), default="mcp"
    )
    parser.add_argument("--model", default=None, help="Model name, in the harness's own format.")
    parser.add_argument("--filter", default=None, help="Substring match on id, persona or axis.")
    parser.add_argument(
        "--prompt",
        default=None,
        help="Run one ad-hoc prompt instead of the library (unscored; keeps the evidence).",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case (default 1).")
    parser.add_argument("--timeout", type=float, default=600.0, help="Seconds per run.")
    parser.add_argument("--bin", default=None, help="Path to the harness executable.")
    parser.add_argument(
        "--server-dir",
        type=Path,
        default=REPO_ROOT,
        help="Checkout of the server to run (e.g. a worktree of an older commit, for A/B).",
    )
    parser.add_argument("--label", default=None, help="Name for this run in reports.")
    parser.add_argument(
        "--harness-arg",
        action="append",
        default=[],
        help="Extra argument for the harness CLI (repeatable), e.g. -c model_reasoning_effort=medium.",
    )
    parser.add_argument(
        "--work-root",
        "--scratch",  # its earlier name, kept so recorded commands still run
        dest="work_root",
        type=Path,
        default=default_work_root(),
        help=(
            f"Where each run's working directory and HOME are made (default: ${WORK_ROOT_ENV}, "
            f"else {DEFAULT_WORK_ROOT}). Must be outside any git repository, and should be "
            "outside the home folder: Claude Code loads the .claude/ and CLAUDE.md of every "
            "directory above its working directory. Its name should say nothing about the "
            "tools, since the model sees its working directory. "
            f"E.g. {NEUTRAL_WORK_ROOT_EXAMPLE}."
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="Results directory.")
    parser.add_argument("--max-budget-usd", type=float, default=1.0, help="Claude Code, per run.")
    parser.add_argument(
        "--codex-bypass-sandbox",
        action="store_true",
        help="For containers where Codex's own sandbox cannot start; the container is the sandbox.",
    )
    parser.add_argument(
        "--instructions",
        type=Path,
        default=None,
        help="A project instructions file, placed in the working dir as AGENTS.md and CLAUDE.md.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip runs already recorded in --out (for batches interrupted part-way).",
    )
    parser.add_argument(
        "--report", nargs="+", type=Path, default=None, help="Compare finished result dirs."
    )
    return parser


def make_paths_absolute(args: argparse.Namespace) -> None:
    """Make every path argument absolute, before anything uses one.

    Each harness starts in its run's own work dir, and a harness may resolve a
    relative path against that, not against where the runner was started. In
    September 2026 run-batch.sh passed a relative --out: opencode looked for its
    config under the work dir, found none, and ran every run with no server,
    and Codex failed to write its final-message file. Those two paths were fixed
    one by one; resolving every path here means no harness, present or future,
    sees a relative one.
    """
    for name in ("out", "work_root", "server_dir", "instructions"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, Path(value).expanduser().resolve())
    # A bare command name is looked up on PATH. A relative path to the executable would
    # be looked up from the run's work dir, which is where the harness is started.
    if args.bin and any(sep and sep in args.bin for sep in (os.sep, os.altsep)):
        args.bin = str(Path(args.bin).expanduser().resolve())


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    make_paths_absolute(args)

    if args.report:
        return report(args.report)
    if not args.harness:
        parser.error("--harness is required unless --report is given")

    adapter_cls = ADAPTERS[args.harness]
    if args.condition not in adapter_cls.conditions:
        parser.error(f"{args.harness} does not support --condition {args.condition}")
    free = args.harness == "opencode" and (args.model or "").endswith("-free")
    if free and args.condition in {"mcp-only", "none"}:
        # Observed: with its own tools switched off, opencode's free tier answers
        # 403 "can only be used from within OpenCode". Use a provider key instead.
        parser.error(f"opencode's free models refuse --condition {args.condition}")
    adapter = adapter_cls(
        args.bin,
        args.model,
        {
            "max_budget_usd": args.max_budget_usd,
            "codex_bypass_sandbox": args.codex_bypass_sandbox,
            "harness_args": args.harness_arg,
        },
    )

    if args.prompt:
        slug = "-".join(args.prompt.lower().split()[:5])
        cases = [
            {
                "id": "adhoc-" + "".join(c for c in slug if c.isalnum() or c == "-"),
                "persona": "adhoc",
                "axis": "adhoc",
                "prompt": args.prompt,
                "max_turns": 10,
            }
        ]
    else:
        cases = load_cases(args.filter, include_network=True)
    if args.condition not in WITH_SERVER and not args.prompt:
        # Without the server, only a checked answer or file can be scored.
        cases = [c for c in cases if c.get("answer") or c.get("artifacts")]
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    label = args.label or "-".join(
        part
        for part in (args.harness, args.model and args.model.split("/")[-1], args.condition)
        if part
    )
    out = args.out or RESULTS_DIR / f"harness-{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} case(s) x {args.repeat} in {label} -> {out}")
    # Recorded with the results, so a reader can tell whether the runs could see it.
    inherited = [str(path) for path in warn_about_inherited_config(args.work_root)]
    hint_words = warn_about_hint_words(args.work_root)

    records = []
    done: set[tuple[str, int]] = set()
    if args.resume and (out / "results.jsonl").is_file():
        for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            records.append(record)
            done.add((record["case_id"], record["repeat"]))
        print(f"resuming: {len(done)} run(s) already recorded")
    elif (out / "results.jsonl").is_file():
        (out / "results.jsonl").unlink()
    with (out / "results.jsonl").open("a", encoding="utf-8") as sink:
        for case in cases:
            for k in range(1, args.repeat + 1):
                if (case["id"], k) in done:
                    continue
                record = run_case(
                    adapter,
                    case,
                    args.condition,
                    k,
                    out,
                    args.server_dir,
                    args.timeout,
                    args.work_root,
                    args.instructions,
                )
                records.append(record)
                sink.write(json.dumps(record) + "\n")
                sink.flush()
                if record["passed"]:
                    verdict = "PASS"
                elif record["upstream_outage"]:
                    verdict = "OUTG"  # an upstream API failed; not scored
                else:
                    verdict = "FAIL" if record["scored"] else "----"
                detail = record["error"] or record.get("answer_detail") or ""
                tools = ",".join(record["called_tools"]) or "-"
                # Flag a run whose server never connected: its tool score says nothing about the model.
                server = " NO-SERVER" if record.get("server_connected") is False else ""
                print(
                    f"  {verdict} {case['id']:36} r{k} {record['duration_s']:>6.1f}s{server} "
                    f"tools={tools[:60]} {detail[:60]}",
                    flush=True,
                )

    summary = summarise(records)
    (out / "summary.json").write_text(
        json.dumps(
            {
                "label": label,
                "args": _jsonable(vars(args)),
                "inherited_agent_config": inherited,
                "work_root_hint_words": hint_words,
                "summary": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, Path) else v for k, v in values.items()}


if __name__ == "__main__":
    raise SystemExit(main())
