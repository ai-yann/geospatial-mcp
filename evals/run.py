#!/usr/bin/env python3
"""Run the tool-selection eval library.

Two modes, because they answer different questions:

``validate`` (no API key)
    Executes each case's expected tool chain against a live in-process server
    and checks the case's assertions. This proves the tools *can* answer every
    prompt and that the library has not drifted from the server -- a renamed
    tool or changed field breaks it immediately. It says nothing about whether
    a model would pick those tools.

``agent`` (needs ANTHROPIC_API_KEY)
    Gives a model the tool catalogue and the prompt alone, lets it choose, and
    scores which tools it called, whether the arguments validated, and whether
    the final answer satisfies the case rubric. This is the real measure of
    tool-selection quality.

Usage::

    python evals/run.py --mode validate
    python evals/run.py --mode validate --network
    python evals/run.py --mode agent --model claude-sonnet-5
    python evals/run.py --mode agent --filter telecom
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

sys.path.insert(0, str(Path(__file__).parent))

from answers import check_answer  # noqa: E402

LIBRARY = Path(__file__).parent / "prompts.yaml"
RESULTS_DIR = Path(__file__).parent / "results"

#: Gates from the README. A run below any of these is a regression.
GATES = {
    "tool_selection_f1": 0.90,
    "first_call_accuracy": 0.85,
    "argument_validity": 0.95,
    "answer_correctness": 0.85,
}
MAX_DISTRACTOR_RATE = 0.05


#: Substrings that identify an upstream outage rather than a defect here.
#: These are scored separately: a free public API being saturated is not a
#: regression in this repository, and treating it as one trains people to
#: ignore the eval.
UPSTREAM_FAILURE_MARKERS = (
    "timed out",
    "mirrors failed",
    "rate-limited",
    "Could not reach",
    "returned HTTP 5",
    "temporarily unavailable",
)


def _is_upstream_outage(detail: str) -> bool:
    return any(marker in detail for marker in UPSTREAM_FAILURE_MARKERS)


@dataclass
class CaseResult:
    case_id: str
    persona: str
    axis: str
    passed: bool
    detail: str = ""
    called_tools: list[str] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    forbidden_called: list[str] = field(default_factory=list)
    first_call_correct: bool | None = None
    duration_s: float = 0.0
    skipped: bool = False
    upstream_outage: bool = False
    tool_selection_f1: float | None = None
    invalid_argument_calls: int = 0
    answer_ok: bool | None = None


def load_cases(filter_text: str | None, include_network: bool) -> list[dict[str, Any]]:
    cases = yaml.safe_load(LIBRARY.read_text())["cases"]
    if filter_text:
        needle = filter_text.lower()
        cases = [
            c
            for c in cases
            if needle in c["id"].lower()
            or needle in c["persona"].lower()
            or needle in c["axis"].lower()
            or needle in " ".join(c.get("tags", [])).lower()
        ]
    if not include_network:
        cases = [c for c in cases if not c.get("network")]
    return cases


# --------------------------------------------------------------- validate mode
def _check_assertion(expression: str, result: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate one assertion against a tool result.

    The namespace exposes the result's top-level keys directly, plus ``result``
    for the whole payload, so assertions read naturally:
    ``"distance_km > 0"`` rather than ``"result['distance_km'] > 0"``.
    """
    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "round": round,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
    }
    namespace: dict[str, Any] = {"result": result, **safe_builtins}
    namespace.update({k: v for k, v in result.items() if k.isidentifier()})

    meta = result.get("meta")
    if isinstance(meta, dict):
        namespace["meta"] = type("Meta", (), meta)  # allow meta.offline
    try:
        return bool(eval(expression, {"__builtins__": {}}, namespace)), ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def run_validate(cases: list[dict[str, Any]]) -> list[CaseResult]:
    from mcp import Client

    from geospatial_mcp.server import build_server

    results: list[CaseResult] = []
    # Cases may need a toolset that is off by default; group them so each
    # server is built once rather than per case.
    by_toolsets: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        key = ",".join(sorted(case.get("toolsets", []))) or "default"
        by_toolsets.setdefault(key, []).append(case)

    for toolsets, group in by_toolsets.items():
        async with Client(build_server(toolsets)) as client:
            results.extend(await _validate_group(client, group))
    return results


async def _validate_group(client: Any, cases: list[dict[str, Any]]) -> list[CaseResult]:
    results: list[CaseResult] = []
    if True:
        available = {t.name for t in (await client.list_tools()).tools}

        for case in cases:
            started = time.monotonic()
            problems: list[str] = []
            called: list[str] = []

            # 1. The library must not reference tools that do not exist.
            for name in case.get("expect_tools", []) + case.get("forbid_tools", []):
                if name not in available:
                    problems.append(f"references unknown tool {name!r}")

            # 2. Each step of the expected chain must run and satisfy its asserts.
            for step in case.get("validate", []):
                tool = step["tool"]
                if tool not in available:
                    problems.append(f"validate step uses unknown tool {tool!r}")
                    continue
                called.append(tool)
                response = await client.call_tool(tool, step.get("args", {}))
                if response.is_error:
                    text = "".join(getattr(b, "text", "") for b in (response.content or []))
                    problems.append(f"{tool} errored: {text[:160]}")
                    continue
                payload = response.structured_content or {}
                for expression in step.get("assert", []):
                    ok, why = _check_assertion(expression, payload)
                    if not ok:
                        problems.append(f"{tool}: assertion failed {expression!r} {why}")

            detail = "; ".join(problems)
            outage = bool(problems) and _is_upstream_outage(detail)
            results.append(
                CaseResult(
                    case_id=case["id"],
                    persona=case["persona"],
                    axis=case["axis"],
                    passed=not problems,
                    detail=detail,
                    called_tools=called,
                    expected_tools=case.get("expect_tools", []),
                    duration_s=time.monotonic() - started,
                    upstream_outage=outage,
                )
            )
    return results


# ------------------------------------------------------------------ agent mode
async def run_agent(cases: list[dict[str, Any]], model: str) -> list[CaseResult]:
    """Give a model the catalogue and the prompt, and score what it chooses."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        print(
            "agent mode needs the anthropic SDK:  uv pip install anthropic",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("agent mode needs ANTHROPIC_API_KEY to be set.", file=sys.stderr)
        raise SystemExit(2)

    from mcp import Client

    from geospatial_mcp.server import build_server

    anthropic = AsyncAnthropic()
    results: list[CaseResult] = []

    async with Client(build_server("default")) as client:
        catalogue = (await client.list_tools()).tools
        tool_specs = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.input_schema,
            }
            for t in catalogue
        ]

        for case in cases:
            if case.get("harness_only"):
                continue  # needs file writes; see evals/harness.py
            started = time.monotonic()
            messages: list[dict[str, Any]] = [{"role": "user", "content": case["prompt"]}]
            called: list[str] = []
            invalid_arguments = 0
            final_text = ""

            for _ in range(case.get("max_turns", 6)):
                response = await anthropic.messages.create(
                    model=model,
                    max_tokens=2048,
                    tools=tool_specs,
                    messages=messages,
                )
                blocks = response.content
                messages.append({"role": "assistant", "content": blocks})
                tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
                final_text += "".join(
                    getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text"
                )
                if not tool_uses:
                    break

                tool_results = []
                for use in tool_uses:
                    called.append(use.name)
                    outcome = await client.call_tool(use.name, use.input or {})
                    if outcome.is_error:
                        invalid_arguments += 1
                    body = outcome.structured_content or "".join(
                        getattr(b, "text", "") for b in (outcome.content or [])
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": use.id,
                            "content": _clip(json.dumps(body)),
                            "is_error": bool(outcome.is_error),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})

            expected = set(case.get("expect_tools", []))
            optional = set(case.get("optional_tools", []))
            forbidden = set(case.get("forbid_tools", []))
            called_set = set(called)
            forbidden_called = sorted(called_set & forbidden)

            hit = expected & called_set
            precision_pool = called_set - optional
            precision = len(hit) / len(precision_pool) if precision_pool else 0.0
            recall = len(hit) / len(expected) if expected else 1.0
            f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

            answer_ok, answer_detail = check_answer(case.get("answer"), final_text)
            passed = (
                bool(expected <= called_set) and not forbidden_called and answer_ok is not False
            )
            detail = ""
            if not expected <= called_set:
                detail = f"missed {sorted(expected - called_set)}"
            if forbidden_called:
                detail += f" called forbidden {forbidden_called}"
            if invalid_arguments:
                detail += f" ({invalid_arguments} invalid tool calls)"
            if answer_ok is False:
                detail += f" answer: {answer_detail}"

            results.append(
                CaseResult(
                    case_id=case["id"],
                    persona=case["persona"],
                    axis=case["axis"],
                    passed=passed,
                    detail=detail.strip(),
                    called_tools=called,
                    expected_tools=sorted(expected),
                    forbidden_called=forbidden_called,
                    first_call_correct=(called[0] in expected | optional) if called else False,
                    duration_s=time.monotonic() - started,
                    tool_selection_f1=round(f1, 4),
                    invalid_argument_calls=invalid_arguments,
                    upstream_outage=_is_upstream_outage(detail),
                    answer_ok=answer_ok,
                )
            )
            print(f"  {'PASS' if passed else 'FAIL'}  {case['id']:38} {detail[:70]}")
    return results


#: Tool results are passed whole up to this size. The old 6,000-character cut
#: sliced geometry mid-JSON, so the model was scored on data it could not parse.
MAX_RESULT_CHARS = 100_000


def _clip(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"... [cut by the eval runner at {MAX_RESULT_CHARS} chars]"


# ------------------------------------------------------------------ reporting
def report(results: list[CaseResult], mode: str, model: str | None) -> int:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    outages = [r for r in results if r.upstream_outage]
    scored = total - len(outages)
    print()
    print("=" * 78)
    print(f"  {mode} mode" + (f" ({model})" if model else ""))
    print("=" * 78)

    by_axis: dict[str, list[CaseResult]] = {}
    by_persona: dict[str, list[CaseResult]] = {}
    for r in results:
        by_axis.setdefault(r.axis, []).append(r)
        by_persona.setdefault(r.persona, []).append(r)

    print(f"\n  overall: {passed}/{total} passed ({100 * passed / total:.0f}%)")
    if outages:
        print(
            f"  of which {len(outages)} failed on an unavailable upstream service, "
            f"not a defect here -> {passed}/{scored} "
            f"({100 * passed / scored:.0f}%) of scorable cases"
        )
    print()
    print("  by axis:")
    for axis, group in sorted(by_axis.items()):
        ok = sum(1 for r in group if r.passed)
        print(f"    {axis:16} {ok:>2}/{len(group):<2}  {'.' * ok}{'x' * (len(group) - ok)}")
    print("\n  by persona:")
    for persona, group in sorted(by_persona.items()):
        ok = sum(1 for r in group if r.passed)
        print(f"    {persona:20} {ok:>2}/{len(group)}")

    failures = [r for r in results if not r.passed and not r.upstream_outage]
    if failures:
        print(f"\n  {len(failures)} failing case(s):")
        for r in failures:
            print(f"    {r.case_id}")
            print(f"      {r.detail[:170]}")
    if outages:
        print(f"\n  {len(outages)} case(s) blocked by an upstream outage:")
        for r in outages:
            print(f"    {r.case_id}: {r.detail[:110]}")

    if mode == "agent":
        first_ok = sum(1 for r in results if r.first_call_correct)
        distractors = sum(1 for r in results if r.forbidden_called)
        scores = [r.tool_selection_f1 for r in results if r.tool_selection_f1 is not None]
        mean_f1 = sum(scores) / len(scores) if scores else 0.0
        total_calls = sum(len(r.called_tools) for r in results)
        bad_calls = sum(r.invalid_argument_calls for r in results)
        validity = 1.0 - (bad_calls / total_calls) if total_calls else 1.0

        print("\n  metrics:")
        answered = [r.answer_ok for r in results if r.answer_ok is not None]
        rows = [
            ("tool-selection F1", mean_f1, GATES["tool_selection_f1"], "min"),
            ("first-call accuracy", first_ok / total, GATES["first_call_accuracy"], "min"),
            ("argument validity", validity, GATES["argument_validity"], "min"),
            ("distractor rate", distractors / total, MAX_DISTRACTOR_RATE, "max"),
        ]
        if answered:
            rows.append(
                (
                    "answer correctness",
                    sum(answered) / len(answered),
                    GATES["answer_correctness"],
                    "min",
                )
            )
        for label, value, gate, direction in rows:
            ok = value >= gate if direction == "min" else value <= gate
            flag = "PASS" if ok else "FAIL"
            comparator = ">=" if direction == "min" else "<="
            print(f"    {label:22} {value:.2f}  ({comparator} {gate})  {flag}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-{mode}{'-' + model if model else ''}.json"
    out.write_text(
        json.dumps(
            {
                "mode": mode,
                "model": model,
                "total": total,
                "passed": passed,
                "cases": [r.__dict__ for r in results],
            },
            indent=2,
        )
    )
    print(f"\n  written to {out.relative_to(REPO_ROOT)}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate", "agent"), default="validate")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--filter", default=None, help="Substring match on id, persona or axis.")
    parser.add_argument(
        "--network",
        action="store_true",
        help="Include cases that call live external APIs.",
    )
    args = parser.parse_args()

    cases = load_cases(args.filter, args.network)
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2
    print(f"running {len(cases)} case(s) in {args.mode} mode")

    if args.mode == "validate":
        results = asyncio.run(run_validate(cases))
        return report(results, "validate", None)
    results = asyncio.run(run_agent(cases, args.model))
    return report(results, "agent", args.model)


if __name__ == "__main__":
    raise SystemExit(main())
