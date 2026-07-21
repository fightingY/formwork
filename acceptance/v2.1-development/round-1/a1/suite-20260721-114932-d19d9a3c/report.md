# miniCC eval report

Overall: PASS
Suite: `suite-20260721-114932-d19d9a3c`
Milestone: `v2.1-development`
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
- git_commit: `b6502e8f51b45fb058e189977f5b6c8e1db6efa8`
- worktree_dirty: `False`
- release_gate: `True`
- milestone: `v2.1-development`
- context_variant: `a1`
- compaction_strategy: `semantic`
- max_prompt_chars: `120000`
- recent_turns: `6`

## Case Summary

- V21_C02_fix_failing_test: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, policy_clear=3/3, avg_turns=6.67, avg_bash_actions=5.67, avg_duration_ms=148987, diff_paths=['src/calculator.py']

## debugging attempt 1: PASS
V2.1 在调试任务中保留关键文件、根因指纹和 patch 验证状态。
Run: `eval-V21_C02_fix_failing_test-r1-20260721-114932-7c8d8a04`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=8, bash_actions=7, policy_denials=0, duration_ms=183712
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 2: PASS
V2.1 在调试任务中保留关键文件、根因指纹和 patch 验证状态。
Run: `eval-V21_C02_fix_failing_test-r2-20260721-115240-fefcafc2`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=172174
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []

## debugging attempt 3: PASS
V2.1 在调试任务中保留关键文件、根因指纹和 patch 验证状态。
Run: `eval-V21_C02_fix_failing_test-r3-20260721-115536-658fd991`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=91075
- PASS command: command exit_code=0, expected=0: python -m unittest discover -s tests
- PASS diff_allowlist: changed files outside allowlist: []
- PASS file_not_contains: unexpected patterns in src/calculator.py: []
