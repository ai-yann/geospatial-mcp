# Tool-selection evals

Unit tests prove the tools compute correct answers. They say nothing about
whether an agent handed a real question will *pick the right tool*. That is a
property of the tool names, descriptions and parameter design, and it is the
thing most likely to silently regress when a description is reworded.

This directory holds a prompt library and three ways to run it.

## Three runners

| Runner | Needs | What it proves |
|---|---|---|
| `run.py --mode validate` | nothing | Every case references tools that exist, and the expected tool chain actually produces an answer satisfying the case's assertions. Catches drift between the library and the server. |
| `run.py --mode agent` | `ANTHROPIC_API_KEY` | Whether a model, given only the tool catalogue and the prompt in a bare API loop, selects the expected tools and reaches a correct answer. |
| `harness.py` | the harness, and its model's key | The same, inside a real agent harness -- Claude Code, Codex or opencode -- with that harness's own prompt and tools, including a shell the model can use instead. |

`validate` is the cheap gate that runs in CI. `agent` is a quick, single-vendor
measure. `harness.py` is closest to what people actually run, and it can show what
the other two cannot: a model skipping this server to do the maths itself.

## Running

```bash
# Real harnesses. Each run gets a fresh HOME and only whitelisted variables,
# so no ambient credential is used by accident. Work outside your home folder,
# in a folder whose name says nothing about the tools (see "Where runs work").
export GEO_EVALS_WORK_ROOT=/Users/Shared/agent-runs
uv run python evals/harness.py --harness opencode --model opencode/<model> --filter demo
uv run python evals/harness.py --harness claude-code --condition builtin --repeat 3
uv run python evals/harness.py --harness codex --model <model>
uv run python evals/harness.py --report evals/results/harness-*   # compare runs

uv run python evals/run.py --mode validate            # offline cases only
uv run python evals/run.py --mode validate --network  # include live-API cases
uv run python evals/run.py --mode agent --model claude-sonnet-5
uv run python evals/run.py --mode agent --filter telecom   # one persona
```

## Where runs work

Each run's working directory and HOME are made in a fresh directory under the
work root. Set it with `--work-root` (formerly `--scratch`, which still works)
or `GEO_EVALS_WORK_ROOT`. The default is `~/.cache/geospatial-evals`.

Use a work root outside your home folder, with a name that says nothing about
the tools, the server or the subject, such as `/Users/Shared/agent-runs` on
macOS. Two things about it reach the model: what is above it, and its name.

**What is above it.** Claude Code reads the `.claude/` folder and the
`CLAUDE.md` of every directory above its working directory, whatever `HOME` is
set to. Under the home folder it finds your own `~/.claude`, and every run gets
your personal skills, agents and instructions. Those are not part of the setup
being measured, and they differ from one machine to the next.

The harness prints a warning when it finds `.claude`, `CLAUDE.md`,
`CLAUDE.local.md`, `AGENTS.md`, `.opencode` or `opencode.json` in the work
root or any directory above it. It also lists them in the results'
`summary.json`, under `inherited_agent_config`. The work root must also be
outside any git repository; the harness refuses to run inside one.

**Its name.** Claude Code shows the model its working directory, and on one
case the folder's name decided whether the model used this server. In October
2026 we ran `telecom-cell-area-sum-007` in Claude Code 2.1.296 with
`claude-sonnet-5-5` at medium effort, the server connected in every run, and
changed only the work root:

| Work root | Runs | Called `rf_link_budget` and passed |
|---|---|---|
| `/Users/Shared/geospatial-evals` | 11 | 6 |
| `/Users/Shared/evals-neutral` and two folders named `eval-scratch` | 20 | 0 |

Every failing run estimated the area by hand, with no tool call. Six of 11
against none of 20 is unlikely to be chance (Fisher's exact test, p < 0.001),
but it is one case, one model and 31 runs. It shows that a folder name can
decide whether the tools are used at all; it does not say how far the name
moves any other case, model or harness. The neutral folders had "eval" and
"evals" in them, so the word that made the difference here was "geospatial".

The harness warns on stderr when any part of the work root's path contains a
word that could hint at what is being tested: `geospatial`, `spatial`, `gis`,
`geo`, `mcp`, `eval`, `evals`, `evaluation`, `tool` or `tools`, or a domain
that starts this server's tool names, such as `rf`, `coord` or `terrain` (but
not `data` or `env`, which name ordinary folders). Only whole words count, so
`george` does not. It lists them in `summary.json` under `work_root_hint_words`.

The default work root carries the hint: `~/.cache/geospatial-evals` contains
"geospatial" and "evals". It stays the default so that new runs compare with
those recorded under it, and the harness warns when it is used. Pass a neutral
`--work-root` to measure without the hint.

## Coverage axes

The library is organised so that each case tests one thing that could break:

1. **Persona breadth** — the anti-narrowing check. Every case is drawn from a
   different industry: logistics, telecom, agriculture, insurance, real estate,
   utilities, emergency response, surveying, maritime, consumer. If the tool
   surface only makes sense to one audience, these fail.
2. **Disambiguation pairs** — near-neighbour tools that are easy to confuse.
   *"How far apart"* must reach `geom_measure`; *"how long to drive"* must reach
   `route_directions`. Each pair appears twice, once for each side.
3. **Multi-tool chains** — questions needing three or more calls with real data
   dependencies, where an early wrong turn compounds.
4. **Input polymorphism** — the same question asked with decimal degrees, DMS,
   a grid reference, a Plus Code and a place name must reach the same tool.
5. **Error recovery** — a deliberately malformed input. Does the error message
   let the model correct itself within the turn budget?
6. **Gap awareness** — a capability that is switched off. The model should call
   `geo_capabilities` and report what to enable, not invent a tool or claim the
   task is impossible.
7. **Correctness** — a domain rule the tool must enforce: a buffer drawn in
   degrees, a hexagonal cell area summed as if it were a circle. Checked on the
   answer, and on any file the agent writes.
8. **Caveats** — uncertainty the model must pass on: an empty result that is
   missing data rather than a true zero, a propagation model used out of range.

Cases tagged `demo` are the chains shown in talks; `--filter demo` runs them.

## Harness conditions

`harness.py --condition` sets what the model has to work with:

| Condition | Tools | Question it answers |
|---|---|---|
| `mcp` | the harness's own + this server | Does the model pick this server's tools, and get it right? |
| `builtin` | the harness's own (shell, files, web) | Can it get there by writing code instead? |
| `none` | none | What does the bare model say? |

Only cases with an `answer` or `artifacts` block are run without the server,
since tool selection cannot be scored there.

Tool calls are read from the server's own trace (`GEO_TRACE_FILE`), which
records every call as the client sent it, before validation. It is the same
for every harness, so no harness's log format decides a score.

The runs in `results/` predate three changes to how `harness.py` starts things,
made in October 2026:

- The server is started as `uv run --directory <server-dir> python -m
  geospatial_mcp`, not with the `geospatial-mcp` console script, so it works
  whatever the command is called. The code that runs is the same.
- `GEO_TRACE_FILE` is passed as an absolute path. A relative `--out` with
  `--server-dir` used to write the trace under the server directory, so
  `cc-ab-before-mcp` has no `trace.jsonl` and its tool calls were taken from
  the harness's own transcript instead.
- Codex's `-o` file is an absolute path. Codex used to resolve it against its
  working directory and log "Failed to write last message file", and the final
  answer was then taken from the last agent message instead.

They also predate `--work-root`. All 108 Claude Code runs recorded in
September 2026 (the `cc-*` directories in `results/`) worked under
`~/.cache/geospatial-evals`, and the init event of every one lists the
operator's own skills and agents from `~/.claude`. Claude Code very probably
loaded `~/.claude/CLAUDE.md` as instructions too, though the transcripts do
not record which instruction files it read.

That folder's name also carries "geospatial" (see "Where runs work"), so the
with-server tool use and pass rates of those runs may be higher than a neutral
folder would give. This was measured for Sonnet 5.5 on one case only; the
September runs used Sonnet 5 and Opus 5.5, and were not repeated under a
neutral folder. The Codex and opencode runs in `results/` worked under the same
folder, and the effect was not measured for those harnesses. Read all of those
results with this in mind.

## Scoring

| Metric | Definition | Gate |
|---|---|---|
| Tool-selection F1 | called set vs `expect_tools` | ≥ 0.90 |
| First-call accuracy | first call is in `expect_tools` | ≥ 0.85 |
| Distractor rate | any `forbid_tools` was called | ≤ 0.05 |
| Argument validity | calls passing schema validation first try | ≥ 0.95 |
| Recovery rate | error cases resolved within `max_turns` | ≥ 0.80 |
| Answer correctness | the case's `answer` checks: patterns, and numbers within tolerance (see `answers.py`) | ≥ 0.85 |
| Pass every repeat | share of cases passing on all `--repeat` runs (harness runner) | reported |

When a case fails, the fix is usually a tool *description*, not code. Feed the
failing transcripts back and rewrite the description that misled the model.
