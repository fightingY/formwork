# miniCC eval report

Overall: PASS
Suite: `suite-20260819-191732-986a8475`
Milestone: `v3.4-acceptance`
Stage: `development_precheck`
Repeat: 3

## Configuration

- base_url: `https://api.siliconflow.cn/v1`
- model: `deepseek-ai/DeepSeek-V4-Flash`
- temperature: `0.0`
- stream: `True`
- include_usage: `True`
- sandbox_mode: `locked`
- execute_local: `True`
- json_mode: `True`
- max_completion_tokens: `2048`
- provider_max_retries: `2`
- provider_timeout_sec: `300.0`
- cache_scope_sha256: `c33df17ff86489f50fe13d889c018f09e82f94aa3ef615049bdbf91f2aed1555`
- docker_image: `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0`
- git_commit: `43cc5a3e8af670edf402001e6a969b918058bfc4`
- worktree_dirty: `True`
- release_gate: `False`
- milestone: `v3.4-acceptance`
- context_variant: `configured`
- cache_variant: `configured`
- cache_sequence_id: `None`
- execution_order: `None`
- guidance_variant: `configured`
- guidance_sequence_id: `None`
- guidance_execution_order: `None`
- guidance_feedback_path: `None`
- feedback_memory_mode: `configured`
- prompt_layout: `append_until_compaction`
- compaction_strategy: `deterministic`
- system_prefix_sha256: `c1efea672a7500aeb92605828c539a160db25c1cfc87f102a7bb611c5641f809`
- max_prompt_chars: `120000`
- recent_turns: `6`
- semantic_max_completion_tokens: `2048`
- case_contexts: `{'R01_cache_delete_retry_boundary': {'retention_markers': []}, 'R02_retry_policy_regression_test': {'retention_markers': []}, 'R03_cache_key_builder_feature': {'retention_markers': []}}`
- case_authority_profiles: `{'R01_cache_delete_retry_boundary': {'source_path': 'eval_cases/real_project_suite_v1/R01_cache_delete_retry_boundary/case.yaml', 'fixture_source_path': 'eval_cases/real_project_suite_v1/R01_cache_delete_retry_boundary/fixture', 'case_definition_sha256': '56b47b64e307374a640565d1eabdeeb0e7649189813cc7563602726cf9df97f2', 'fixture_content_sha256': '75955656fbc68163c3abe3477f1ab3c066db64dac4d885f7694ab8ea8bdab496'}, 'R02_retry_policy_regression_test': {'source_path': 'eval_cases/real_project_suite_v1/R02_retry_policy_regression_test/case.yaml', 'fixture_source_path': 'eval_cases/real_project_suite_v1/R02_retry_policy_regression_test/fixture', 'case_definition_sha256': '2e168d0fcd14351589a0c6453adf406c942af72bbbb307a9d3ee19bbc81d890b', 'fixture_content_sha256': '698724288b516c60c977f1d2f9177736af1cc20b4725e9ba35d1a9c7f09a08ad'}, 'R03_cache_key_builder_feature': {'source_path': 'eval_cases/real_project_suite_v1/R03_cache_key_builder_feature/case.yaml', 'fixture_source_path': 'eval_cases/real_project_suite_v1/R03_cache_key_builder_feature/fixture', 'case_definition_sha256': '28dee20fececd316f235204159987d9310814639c6d3c19719ee30864d253639', 'fixture_content_sha256': 'd04bbf0857c537d7eea159e76bbe63f6a044f936234bba8c3bb201270c60f9c6'}}`
- case_authority_bundle_sha256: `6d88700baf74cf24eaa0a2ab2985a732ee83bd569df41da2957c5e665dcebb7c`
- git_preflight_verified: `False`
- git_postflight_verified: `False`

## Case Summary

- R01_cache_delete_retry_boundary: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, policy_clear=3/3, avg_turns=5.33, avg_bash_actions=4.33, avg_duration_ms=65339, diff_paths=['CacheDeleteMessage.java']
- R02_retry_policy_regression_test: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, policy_clear=3/3, avg_turns=5.00, avg_bash_actions=4.00, avg_duration_ms=74225, diff_paths=['RetryPolicyBoundaryTest.java']
- R03_cache_key_builder_feature: 3/3 passed (pass_rate=1.000), task=3/3, agent=3/3, infrastructure=3/3, policy_clear=3/3, avg_turns=5.00, avg_bash_actions=4.00, avg_duration_ms=87355, diff_paths=['ShopCacheKeyBuilder.java']

## real_project_debugging attempt 1: PASS
Agent 能在真实缓存重试语义中定位状态更新顺序错误，并通过独立 Java 行为验证。
Run: `eval-R01_cache_delete_retry_boundary-r1-20260819-191732-dd7192a8`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=6, bash_actions=5, policy_denials=0, duration_ms=58918
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_test_writing attempt 1: PASS
Agent 补出的测试不只会通过正确实现，还能识别最大重试次数和异常类型的边界 mutant。
Run: `eval-R02_retry_policy_regression_test-r1-20260819-191836-9a389ca6`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=72039
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_feature_work attempt 1: PASS
Agent 能实现带规范化、参数校验和兼容性约束的小型真实缓存功能。
Run: `eval-R03_cache_key_builder_feature-r1-20260819-191955-b5eb90e0`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=182088
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_debugging attempt 2: PASS
Agent 能在真实缓存重试语义中定位状态更新顺序错误，并通过独立 Java 行为验证。
Run: `eval-R01_cache_delete_retry_boundary-r2-20260819-192303-db0191f7`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=37869
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_test_writing attempt 2: PASS
Agent 补出的测试不只会通过正确实现，还能识别最大重试次数和异常类型的边界 mutant。
Run: `eval-R02_retry_policy_regression_test-r2-20260819-192346-470dcb6d`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=53720
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_feature_work attempt 2: PASS
Agent 能实现带规范化、参数校验和兼容性约束的小型真实缓存功能。
Run: `eval-R03_cache_key_builder_feature-r2-20260819-192447-f008d687`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=36486
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_debugging attempt 3: PASS
Agent 能在真实缓存重试语义中定位状态更新顺序错误，并通过独立 Java 行为验证。
Run: `eval-R01_cache_delete_retry_boundary-r3-20260819-192529-dd05ea60`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=99231
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_test_writing attempt 3: PASS
Agent 补出的测试不只会通过正确实现，还能识别最大重试次数和异常类型的边界 mutant。
Run: `eval-R02_retry_policy_regression_test-r3-20260819-192713-588e7d7e`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=96916
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1

## real_project_feature_work attempt 3: PASS
Agent 能实现带规范化、参数校验和兼容性约束的小型真实缓存功能。
Run: `eval-R03_cache_key_builder_feature-r3-20260819-192857-8127df03`
Verdict: `passed`
Outcome: task=PASS, agent=PASS, infrastructure=PASS
Policy outcome: `clear`
Workspace cleaned: `true`
Metrics: turns=5, bash_actions=4, policy_denials=0, duration_ms=43492
- PASS initial_verify: command exit_code=1, expected=1: python verify.py
- PASS command: command exit_code=0, expected=0: python verify.py
- PASS diff_allowlist: changed files outside allowlist: []
- PASS max_changed_files: changed_files=1, limit=1
