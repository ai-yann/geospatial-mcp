Harness transcripts for the parser tests in `tests/evals/test_harness.py`.

- `opencode.jsonl` is a real `opencode run --format json` transcript (tool
  output trimmed), captured with the free Nemotron 3 Ultra model.
- `claude-code.jsonl` and `codex.jsonl` follow each CLI's documented
  `stream-json` / `exec --json` event shapes. Replace them with captured
  transcripts once a run with those harnesses has been recorded.
