# miniCC eval report

Overall: PASS
Suite: `suite-20260720-232453-22af7432`
Milestone: `stable-v2.0.2`
Stage: `formal_acceptance`
Repeat: 3

## Configuration

- base_url: `https://api.siliconflow.cn/v1`
- model: `deepseek-ai/DeepSeek-V4-Flash`
- temperature: `0.0`
- sandbox_mode: `locked`
- execute_local: `False`
- json_mode: `True`
- docker_image: `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0`
- git_commit: `3c1cd53b9fd46681edafcbb256e89241adb55003`
- worktree_dirty: `False`
- release_gate: `True`
- milestone: `stable-v2.0.2`

## Case Summary

- C02_fix_failing_test: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, policy_clear=3/3, avg_turns=5.67, avg_bash_actions=4.67, avg_duration_ms=29499, diff_paths=['src/calculator.py']

## debugging attempt 1: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r1-20260720-232453-814570e0`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=35594
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 2: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r2-20260720-232533-f73f420a`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=26485
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 3: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r3-20260720-232603-bb7cb88b`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=26418
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []
