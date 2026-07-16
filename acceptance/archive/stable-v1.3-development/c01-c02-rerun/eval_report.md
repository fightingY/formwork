# miniCC eval report

Overall: PASS
Repeat: 3

## Configuration

- base_url: `https://api.siliconflow.cn/v1`
- model: `deepseek-ai/DeepSeek-V4-Flash`
- temperature: `0.0`
- sandbox_mode: `locked`
- execute_local: `False`

## Case Summary

- C01_repo_onboarding: 3/3 passed (pass_rate=1.000), avg_turns=7.67, avg_bash_actions=6.67, avg_duration_ms=142564, diff_paths=['ONBOARDING.md']
- C02_fix_failing_test: 3/3 passed (pass_rate=1.000), avg_turns=8.33, avg_bash_actions=7.33, avg_duration_ms=147506, diff_paths=['src/calculator.py']

## repo_understanding attempt 1: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r1-20260714-094950-27f1f070`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=101177
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 1: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r1-20260714-095136-a3cc8b05`
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=107791
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## repo_understanding attempt 2: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r2-20260714-095327-72f46b9d`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=193844
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 2: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r2-20260714-095644-eb84c3be`
Metrics: turns=9, bash_actions=8, policy_denials=0, duration_ms=205955
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## repo_understanding attempt 3: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r3-20260714-100012-2b8a76f6`
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=132672
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 3: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r3-20260714-100227-cb9bed27`
Metrics: turns=9, bash_actions=8, policy_denials=0, duration_ms=128771
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []
