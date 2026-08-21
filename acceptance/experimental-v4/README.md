# V4 Experimental Acceptance

This acceptance bundle records the deterministic implementation gate for the V4
multi-agent harness. It does not promote V4 from experimental to Stable.

Implemented contracts:

- strict `delegate` protocol with role/profile, dependency, cycle, join and budget validation;
- shared runtime capability policy, conservative read-only Bash policy and one-owner write lease;
- in-process and own-process JSONL `childrun` backends with normalized lifecycle failures;
- bounded parallel/dependency workflow coordinator and standard workflow helpers;
- independent child result/evidence summaries, root workflow observation and V4 metrics;
- immutable trace projection to redacted `transcript.jsonl` and `transcript.md`;
- opt-in `multi-agent-v4` loop handling; legacy profiles remain unchanged by default.

## Verification

- `370 passed, 3 skipped` via `.venv/Scripts/python.exe -m pytest -q`
- `ruff check src tests`: pass
- `mypy src/minicc`: pass
- `uv build`: pass (`dist/mini_claude_code-3.6.0.{whl,tar.gz}`)
- focused V4 tests: `4 passed`

The skipped tests are the repository's pre-existing optional Docker integration
tests. No real-provider success-rate claim is made by this bundle.
