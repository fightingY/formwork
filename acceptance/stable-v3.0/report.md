# miniCC V3.0 Release Evidence Report

Status: **PASS**
Source commit: `cc150b0ae815e2add2f4ac036b3e0371205ddda4`

## Dimensions

| Dimension | State | Result | Runs | Headline |
| --- | --- | --- | ---: | --- |
| system_regression | stable | PASS | 15 | 15/15 fixed regression runs passed |
| context_governance | stable | PASS | 24 | prompt mean reduced 9.27% and 46.60%; fact retention 100% |
| memory_benefit | stable | PASS | 27 | repeated reads 9 -> 0; follow-up prompt tokens reduced 27.82% |
| checkpoint_resume | stable | PASS | 1 | 1/1 real-model resume passed; duplicate executions 0 |

## Traceable claims

### system-regression-pass-rate

- Claim: Fixed C01/C02/C03/C04/C09 regression runs completed successfully.
- Value: `{"passed_runs": 15, "total_runs": 15, "pass_rate": 1.0}`
- Cases: `["C01_repo_onboarding", "C02_fix_failing_test", "C03_add_cli_option", "C04_add_regression_test", "C09_hitl_destructive_command"]`
- Suites: `[]`
- Runs: `["eval-C01_repo_onboarding-r1-20260802-150630-0a0a3d20", "eval-C02_fix_failing_test-r1-20260802-150714-e713adb1", "eval-C03_add_cli_option-r1-20260802-150733-369c8a6c", "eval-C04_add_regression_test-r1-20260802-150810-8cb764cb", "eval-C09_hitl_destructive_command-r1-20260802-150826-8a9f68dc", "eval-C01_repo_onboarding-r2-20260802-150835-b3d2a955", "eval-C02_fix_failing_test-r2-20260802-150915-a9195756", "eval-C03_add_cli_option-r2-20260802-150936-fb1bbf5f", "eval-C04_add_regression_test-r2-20260802-151009-2dbdce3a", "eval-C09_hitl_destructive_command-r2-20260802-151026-6acb486e", "eval-C01_repo_onboarding-r3-20260802-151034-1a0e4304", "eval-C02_fix_failing_test-r3-20260802-151117-b3986b4c", "eval-C03_add_cli_option-r3-20260802-151136-93a6c6e1", "eval-C04_add_regression_test-r3-20260802-151222-aa6e5491", "eval-C09_hitl_destructive_command-r3-20260802-151238-93dcbdff"]`
- Source: `D:\MyPythonCode\mini-claude-code\.minicc\suites\suite-20260802-150630-4df523ea\report.json`
- Raw artifacts: `["D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C01_repo_onboarding-r1-20260802-150630-0a0a3d20", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C02_fix_failing_test-r1-20260802-150714-e713adb1", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C03_add_cli_option-r1-20260802-150733-369c8a6c", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C04_add_regression_test-r1-20260802-150810-8cb764cb", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C09_hitl_destructive_command-r1-20260802-150826-8a9f68dc", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C01_repo_onboarding-r2-20260802-150835-b3d2a955", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C02_fix_failing_test-r2-20260802-150915-a9195756", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C03_add_cli_option-r2-20260802-150936-fb1bbf5f", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C04_add_regression_test-r2-20260802-151009-2dbdce3a", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C09_hitl_destructive_command-r2-20260802-151026-6acb486e", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C01_repo_onboarding-r3-20260802-151034-1a0e4304", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C02_fix_failing_test-r3-20260802-151117-b3986b4c", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C03_add_cli_option-r3-20260802-151136-93a6c6e1", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C04_add_regression_test-r3-20260802-151222-aa6e5491", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\runs\\eval-C09_hitl_destructive_command-r3-20260802-151238-93dcbdff"]`
- Rerun: `uv run minicc eval eval_cases/capability_suite_v1 --case C01_repo_onboarding --case C02_fix_failing_test --case C03_add_cli_option --case C04_add_regression_test --case C09_hitl_destructive_command --repeat 3`

### context-compaction-prompt-reduction

- Claim: Semantic compaction reduced mean prompt size in both independent rounds while retaining critical facts.
- Value: `{"round_prompt_reduction_rates": [0.09265517566167465, 0.465968845904563], "retention_rate": 1.0}`
- Cases: `["V21_C02_fix_failing_test", "V21_C03_add_cli_option", "V21_C07_large_log_debugging"]`
- Suites: `["suite-20260721-114710-5f07d43c", "suite-20260721-114932-d19d9a3c", "suite-20260727-v21-round2-a0-release-caebc1c", "suite-20260727-v21-round2-a1-release-caebc1c"]`
- Runs: `["eval-V21_C02_fix_failing_test-r1-20260721-114710-f0a484ad", "eval-V21_C02_fix_failing_test-r2-20260721-114752-bab74887", "eval-V21_C02_fix_failing_test-r3-20260721-114815-288725f6", "eval-V21_C02_fix_failing_test-r1-20260721-114932-7c8d8a04", "eval-V21_C02_fix_failing_test-r2-20260721-115240-fefcafc2", "eval-V21_C02_fix_failing_test-r3-20260721-115536-658fd991", "eval-V21_C02_fix_failing_test-r1-20260722-211003-05170056", "eval-V21_C03_add_cli_option-r1-20260722-211045-a0f3fc27", "eval-V21_C07_large_log_debugging-r1-20260722-211144-a4e78d94", "eval-V21_C02_fix_failing_test-r2-20260722-211923-7e8c29fb", "eval-V21_C03_add_cli_option-r2-20260722-212027-de46fe03", "eval-V21_C07_large_log_debugging-r2-20260727-103810-e5d6546e", "eval-V21_C02_fix_failing_test-r3-20260722-213010-db3d1cc1", "eval-V21_C03_add_cli_option-r3-20260722-213128-5e939a96", "eval-V21_C07_large_log_debugging-r3-20260722-213852-75aff6c3", "eval-V21_C02_fix_failing_test-r1-20260722-220111-9ef12be9", "eval-V21_C03_add_cli_option-r1-20260727-103415-8b31e37a", "eval-V21_C07_large_log_debugging-r1-20260722-222216-a06d8a88", "eval-V21_C02_fix_failing_test-r2-20260722-223457-305a442b", "eval-V21_C03_add_cli_option-r2-20260727-104544-72441b63", "eval-V21_C07_large_log_debugging-r2-20260722-225725-6989f9ab", "eval-V21_C02_fix_failing_test-r3-20260722-231113-17ad7cce", "eval-V21_C03_add_cli_option-r3-20260727-095130-ce61246c", "eval-V21_C07_large_log_debugging-r3-20260727-102246-d333e1fc"]`
- Source: `D:\MyPythonCode\mini-claude-code\acceptance\stable-v2.1\context-compaction-ab\report.json`
- Raw artifacts: `["D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260721-114710-5f07d43c\\report.json", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260721-114932-d19d9a3c\\report.json", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260727-v21-round2-a0-release-caebc1c\\report.json", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260727-v21-round2-a1-release-caebc1c\\report.json"]`
- Rerun: `uv run minicc compaction-report --a0 <round1-a0-report> --a1 <round1-a1-report> --a0 <round2-a0-report> --a1 <round2-a1-report> --output-dir <output>`

### working-memory-repeated-read-reduction

- Claim: Explicit-source working memory eliminated repeated source-file reads across all nine follow-up pairs.
- Value: `{"m0_reads": 9, "m1_reads": 0, "prompt_token_reduction_rate": 0.27824177016107166}`
- Cases: `["M01_service_contract_follow_up", "M02_deploy_cli_follow_up", "M03_validator_contract_follow_up"]`
- Suites: `["suite-20260802-130812-5862115e", "suite-20260802-131105-3763ea38", "suite-20260802-131409-441c511f"]`
- Runs: `["eval-M01_service_contract_follow_up_source-r1-20260802-130812-f2134992", "eval-M01_service_contract_follow_up_follow_up_m0-r1-20260802-130832-988d4101", "eval-M01_service_contract_follow_up_follow_up_m1-r1-20260802-130853-b6d47d43", "eval-M01_service_contract_follow_up_source-r2-20260802-130907-bc36e092", "eval-M01_service_contract_follow_up_follow_up_m0-r2-20260802-130934-e903c507", "eval-M01_service_contract_follow_up_follow_up_m1-r2-20260802-130916-a9020197", "eval-M01_service_contract_follow_up_source-r3-20260802-130956-174bdc9a", "eval-M01_service_contract_follow_up_follow_up_m0-r3-20260802-131006-eba82b7c", "eval-M01_service_contract_follow_up_follow_up_m1-r3-20260802-131026-aa12503e", "eval-M02_deploy_cli_follow_up_source-r1-20260802-131105-5731b991", "eval-M02_deploy_cli_follow_up_follow_up_m0-r1-20260802-131117-a99be753", "eval-M02_deploy_cli_follow_up_follow_up_m1-r1-20260802-131141-51fb95a6", "eval-M02_deploy_cli_follow_up_source-r2-20260802-131157-b8d3c880", "eval-M02_deploy_cli_follow_up_follow_up_m0-r2-20260802-131223-1d24c626", "eval-M02_deploy_cli_follow_up_follow_up_m1-r2-20260802-131207-2acbcc5c", "eval-M02_deploy_cli_follow_up_source-r3-20260802-131248-4d7a1664", "eval-M02_deploy_cli_follow_up_follow_up_m0-r3-20260802-131305-b67363a9", "eval-M02_deploy_cli_follow_up_follow_up_m1-r3-20260802-131331-192e3ffd", "eval-M03_validator_contract_follow_up_source-r1-20260802-131409-37f4327a", "eval-M03_validator_contract_follow_up_follow_up_m0-r1-20260802-131418-b5fb84e9", "eval-M03_validator_contract_follow_up_follow_up_m1-r1-20260802-131441-be5097d8", "eval-M03_validator_contract_follow_up_source-r2-20260802-131500-f75f3519", "eval-M03_validator_contract_follow_up_follow_up_m0-r2-20260802-131532-2c8e8780", "eval-M03_validator_contract_follow_up_follow_up_m1-r2-20260802-131511-4a3d801a", "eval-M03_validator_contract_follow_up_source-r3-20260802-131556-cdb24178", "eval-M03_validator_contract_follow_up_follow_up_m0-r3-20260802-131609-f63acec6", "eval-M03_validator_contract_follow_up_follow_up_m1-r3-20260802-131630-82842d5e"]`
- Source: `D:\MyPythonCode\mini-claude-code\acceptance\stable-v2.2\report.json`
- Raw artifacts: `["D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260802-130812-5862115e\\report.json", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260802-131105-3763ea38\\report.json", "D:\\MyPythonCode\\mini-claude-code\\.minicc\\suites\\suite-20260802-131409-441c511f\\report.json"]`
- Rerun: `uv run minicc memory-report --report <M01-report.json> --report <M02-report.json> --report <M03-report.json> --output-dir acceptance/stable-v2.2`

### checkpoint-resume-state-fidelity

- Claim: The real-model interrupted run resumed without duplicating the completed file-creation action.
- Value: `{"resume_count": 1, "duplicate_executions": 0, "workspace_verified": true, "trajectory_verified": true, "diff_verified": true}`
- Cases: `["real_model_checkpoint_resume"]`
- Suites: `[]`
- Runs: `["20260716-220053-493581e9"]`
- Source: `D:\MyPythonCode\mini-claude-code\acceptance\stable-v2.0\checkpoint_report.json`
- Raw artifacts: `["D:\\MyPythonCode\\mini-claude-code\\acceptance\\stable-v2.0\\real-model-run"]`
- Rerun: `uv run minicc run "完成一个小修改并验证" --interrupt-after-steps 1 && uv run minicc resume <run_id> --from-checkpoint`
