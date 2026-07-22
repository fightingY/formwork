# V2.1 Formal Development Archive

Status: **INCONCLUSIVE**

## Evidence

| Variant | Scope | Result | Suite |
|---|---|---:|---|
| A0 | C02/C03/C07, 3 repeats each | 9/9 PASS | `suite-20260722-v21-round2-a0-final-e96655e` |
| A1 | C02, 3 repeats | 3/3 PASS | `suite-20260721-114932-d19d9a3c` |

## Gate decision

The roadmap requires two independent A/B rounds, at least three cases, and an A1 pass rate no lower
than A0. The second A1 three-case round was not completed: C03 exhausted its configured budget in
the pre-fix runs, and the post-fix retry was stopped before acceptance; C07 has no qualifying A1
suite. Therefore no `stable-v2.1` tag is created and semantic compaction remains experimental.

## Implementation verification

- Provider resilience configuration: timeout 300 seconds, two retries, exponential backoff.
- Semantic compaction continuity guard preserves the active goal and unfinished-run state.
- Regression suite: `159 passed`.
- Worktree was clean when the retained A0 suite was generated.

This is the single human-facing V2.1 archive. Intermediate failed/retried outputs are intentionally
not copied into `acceptance/`.
