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

- C01_repo_onboarding: 3/3 passed (pass_rate=1.000), avg_turns=8.00, avg_bash_actions=7.00, avg_duration_ms=86070, diff_paths=['ONBOARDING.md']
- C02_fix_failing_test: 3/3 passed (pass_rate=1.000), avg_turns=6.67, avg_bash_actions=5.67, avg_duration_ms=94686, diff_paths=['src/calculator.py']
- C03_add_cli_option: 3/3 passed (pass_rate=1.000), avg_turns=7.00, avg_bash_actions=6.00, avg_duration_ms=114650, diff_paths=['src/demo_cli.py', 'tests/test_cli.py']
- C04_add_regression_test: 3/3 passed (pass_rate=1.000), avg_turns=6.00, avg_bash_actions=5.00, avg_duration_ms=65926, diff_paths=['tests/test_parser.py']

## repo_understanding attempt 1: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r1-20260713-002945-583b8c58`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=81036
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 1: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r1-20260713-003108-d4c3e829`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=95447
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## feature_work attempt 1: PASS
Agent 能完成小功能实现并补充测试。
Run: `eval-C03_add_cli_option-r1-20260713-003245-720a73e4`
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=176216
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS command: command exit_code=0, expected=0: python -m src.demo_cli greet Alice --json | python -m json.tool
- PASS file_contains: missing patterns in tests/test_cli.py: []
- PASS diff_allowlist: changed files outside allowlist: []

## test_writing attempt 1: PASS
Agent 能只补测试，不乱改业务代码。
Run: `eval-C04_add_regression_test-r1-20260713-003544-bfcd58b2`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=37502
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_contains: missing patterns in tests/test_parser.py: []

## repo_understanding attempt 2: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r2-20260713-003624-aa2475e9`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=90935
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 2: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r2-20260713-003757-2f15ff5e`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=130038
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## feature_work attempt 2: PASS
Agent 能完成小功能实现并补充测试。
Run: `eval-C03_add_cli_option-r2-20260713-004009-a97867f0`
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=83157
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS command: command exit_code=0, expected=0: python -m src.demo_cli greet Alice --json | python -m json.tool
- PASS file_contains: missing patterns in tests/test_cli.py: []
- PASS diff_allowlist: changed files outside allowlist: []

## test_writing attempt 2: PASS
Agent 能只补测试，不乱改业务代码。
Run: `eval-C04_add_regression_test-r2-20260713-004135-57c96e21`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=65619
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_contains: missing patterns in tests/test_parser.py: []

## repo_understanding attempt 3: PASS
Agent 能在不改源码的情况下完成仓库理解和交接文档。
Run: `eval-C01_repo_onboarding-r3-20260713-004243-7d762f83`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=86239
- PASS file_exists: file exists: ONBOARDING.md
- PASS file_contains: missing patterns in ONBOARDING.md: []
- PASS no_source_diff: protected files changed: []

## debugging attempt 3: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r3-20260713-004411-23c6c5e0`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=58572
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## feature_work attempt 3: PASS
Agent 能完成小功能实现并补充测试。
Run: `eval-C03_add_cli_option-r3-20260713-004512-957c2323`
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=84577
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS command: command exit_code=0, expected=0: python -m src.demo_cli greet Alice --json | python -m json.tool
- PASS file_contains: missing patterns in tests/test_cli.py: []
- PASS diff_allowlist: changed files outside allowlist: []

## test_writing attempt 3: PASS
Agent 能只补测试，不乱改业务代码。
Run: `eval-C04_add_regression_test-r3-20260713-004639-84ddbe2b`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=94658
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_contains: missing patterns in tests/test_parser.py: []
