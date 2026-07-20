# miniCC eval report

Overall: PASS
Repeat: 3

## Configuration

- base_url: `https://api.siliconflow.cn/v1`
- model: `deepseek-ai/DeepSeek-V4-Flash`
- temperature: `0.0`
- sandbox_mode: `locked`
- execute_local: `False`
- json_mode: `True`
- docker_image: `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0`
- git_commit: `15713620f67c86dc31b73ac38d0ca969279552e8`
- worktree_dirty: `False`
- release_gate: `True`
- milestone: `stable-v2.0.1`

## Case Summary

- C02_fix_failing_test: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, avg_turns=5.00, avg_bash_actions=4.00, avg_duration_ms=29853, diff_paths=['src/calculator.py']

## debugging attempt 1: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r1-20260720-212003-12a3d4d1`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Metrics: turns=4, bash_actions=3, policy_denials=0, duration_ms=21474
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 2: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r2-20260720-212029-e9192ba5`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Metrics: turns=7, bash_actions=6, policy_denials=0, duration_ms=35564
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 3: PASS
Agent 能读失败信息、定位代码并做最小修复。
Run: `eval-C02_fix_failing_test-r3-20260720-212110-9e5a4e92`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Metrics: turns=4, bash_actions=3, policy_denials=0, duration_ms=32521
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []
