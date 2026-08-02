# miniCC V2.1.2 Prompt Cache Utilization P1/P2 A/B

Status: **PASS**

| Target | Required |
|---|---:|
| Full-chain weighted hit rate | 70.00% |
| Steady-state weighted hit rate | 80.00% |
| Cache capture efficiency | 85.00% |
| Uncached-token reduction before saturation | 40.00% |
| Saturation fallback full-chain hit rate | 80.00% |
| Saturation fallback steady-state hit rate | 90.00% |
| Prompt inflation limit | 10.00% |
| Balanced short-task miss inflation limit | 15.00% |

## Case authority profiles

- `C02_fix_failing_test`: case `69516daab74c8bc6dfdf3e1c5763c5622f535bd03152c98e66a1f5254f1765ac`, fixture `67953ce9a062f8aef439e157c8c0511c94219a88b1eeeaeec204a8890580127a`
- `C07_large_log_debugging`: case `47174de9a7ebf609e5912396c0bd95b7d33c4cf68cbbebc0134b27f618d2fbe5`, fixture `575cad4c334beeab29814c3fc89a1b769071df34bc182aad0b1ac019e2236274`

## Round 1: PASS

- Namespace/order: `formal-v212-round-81` / `p1-first`
- P1/P2 probe: `cache-probe-20260802-085125-7d33dc73` / `cache-probe-20260802-085831-1f91f08e`
- P1/P2 suite: `suite-20260802-085153-6914d10d` / `suite-20260802-085859-d683cc5c`

| Workload | Variant | Requests | Prompt | Hit | Miss | Full-chain | Steady | Capture | Task pass | Prefix resets | Provider latency | E2E wall |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed-long | P1 | 12 | 41270 | 23296 | 17974 | 56.45% | 58.15% | 94.79% | 100.00% | 5 | 720498 ms | 724138 ms |
| fixed-long | P2 | 12 | 36216 | 31744 | 4472 | 87.65% | 94.00% | 96.96% | 100.00% | 0 | 168687 ms | 170183 ms |
| C02_fix_failing_test | P1 | 15 | 13859 | 6912 | 6947 | 49.87% | 64.38% | 69.36% | 100.00% | 0 | 39734 ms | 44530 ms |
| C02_fix_failing_test | P2 | 15 | 14728 | 4864 | 9864 | 33.03% | 50.64% | 45.73% | 100.00% | 0 | 58520 ms | 63232 ms |
| C07_large_log_debugging | P1 | 27 | 65871 | 36352 | 29519 | 55.19% | 62.04% | 78.53% | 100.00% | 6 | 116024 ms | 124425 ms |
| C07_large_log_debugging | P2 | 27 | 70167 | 53248 | 16919 | 75.89% | 83.09% | 89.47% | 100.00% | 0 | 137342 ms | 146072 ms |

### Gate detail

- PASS `comparable_configuration`
- PASS `shared_round_namespace`
- PASS `shared_execution_order`
- PASS `execution_order_verified`
- PASS `formal_clean_evidence`
- PASS `fixed_request_count`
- PASS `fixed_payloads_verified`
- PASS `fixed_metrics_complete`
- PASS `fixed_requests_and_tasks_passed`
- PASS `real_case_matrix_complete`
- PASS `suite_top_level_passed`
- PASS `no_extra_suite_cases`
- PASS `case_authority_profiles_locked`
- PASS `runtime_model_identity_verified`
- PASS `all_tasks_passed`
- PASS `all_cache_metrics_complete`
- PASS `provider_retries_within_budget`
- PASS `p2_fixed_full_chain_at_least_70`
- PASS `p2_long_full_chain_at_least_70`
- PASS `p2_fixed_steady_at_least_80`
- PASS `p2_long_steady_at_least_80`
- PASS `p2_fixed_capture_at_least_85`
- PASS `p2_long_capture_at_least_85`
- PASS `p2_theoretical_full_chain_qualifies`
- PASS `capture_efficiency_bounded`
- PASS `fixed_miss_improvement_or_saturation_target`
- PASS `long_post_slide_miss_reduction_at_least_40`
- PASS `fixed_prompt_inflation_within_10`
- PASS `long_prompt_inflation_within_10`
- PASS `prefix_accounting_complete`
- PASS `retry_cache_penalty_accounted`
- PASS `request_detail_complete`
- PASS `request_aggregates_reconcile`
- PASS `long_tasks_use_exactly_9_requests`
- PASS `long_action_shape_verified`
- PASS `long_post_slide_shape_comparable`
- PASS `p2_key_fact_retention_complete`

### Per-request evidence

<details><summary>fixed-long P1 (12 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| - | 1 | 2550 | 0 | 2550 | 2550 | 0 | 2550 | 1 | True | False | cold_start | 0 | n/a | 17237 | 1 / - |
| - | 2 | 2619 | 2304 | 315 | 2619 | 2304 | 315 | 1 | False | True | exact_append | 2550 | 90.35% | 11980 | 1 / - |
| - | 3 | 2700 | 2560 | 140 | 2700 | 2560 | 140 | 1 | False | True | exact_append | 2619 | 97.75% | 4882 | 1 / - |
| - | 4 | 2779 | 2560 | 219 | 2779 | 2560 | 219 | 1 | False | True | exact_append | 2700 | 94.81% | 4136 | 1 / - |
| - | 5 | 2871 | 2560 | 311 | 2871 | 2560 | 311 | 1 | False | True | exact_append | 2779 | 92.12% | 2418 | 1 / - |
| - | 6 | 2955 | 2816 | 139 | 2955 | 2816 | 139 | 1 | False | True | exact_append | 2871 | 98.08% | 8692 | 1 / - |
| - | 7 | 3051 | 2816 | 235 | 3051 | 2816 | 235 | 1 | False | True | exact_append | 2955 | 95.30% | 7689 | 1 / - |
| - | 8 | 3076 | 2560 | 516 | 3076 | 2560 | 516 | 2 | False | False | recent_window_moved | 0 | n/a | 6481 | 1 / - |
| - | 9 | 3102 | 2560 | 542 | 3102 | 2560 | 542 | 3 | False | False | recent_window_moved | 0 | n/a | 3626 | 1 / - |
| - | 10 | 3107 | 3072 | 35 | 6214 | 0 | 6214 | 4 | False | False | recent_window_moved | 0 | n/a | 325804 | 2 / timeout |
| - | 11 | 3111 | 2560 | 551 | 3111 | 2560 | 551 | 5 | False | False | recent_window_moved | 0 | n/a | 12556 | 1 / - |
| - | 12 | 3121 | 3072 | 49 | 6242 | 0 | 6242 | 6 | False | False | recent_window_moved | 0 | n/a | 314997 | 2 / timeout |

Cache model: steady basis `configured_warmup_requests`; theoretical input/output = 16474 / 16973 tokens; capture input/output = 94.79% / 92.00%; empirical hit block = 256 tokens.

Retry accounting: 2 logical requests retried; 6228 upper-bound physical input tokens added; 6144 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 23296 cached input + 17974 uncached input + 7426 output.

</details>

<details><summary>fixed-long P2 (12 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| - | 1 | 2550 | 0 | 2550 | 2550 | 0 | 2550 | 1 | True | False | cold_start | 0 | n/a | 37115 | 1 / - |
| - | 2 | 2619 | 2560 | 59 | 2619 | 2560 | 59 | 1 | False | True | exact_append | 2550 | 100.00% | 20037 | 1 / - |
| - | 3 | 2700 | 2560 | 140 | 2700 | 2560 | 140 | 1 | False | True | exact_append | 2619 | 97.75% | 14023 | 1 / - |
| - | 4 | 2779 | 2560 | 219 | 2779 | 2560 | 219 | 1 | False | True | exact_append | 2700 | 94.81% | 9280 | 1 / - |
| - | 5 | 2871 | 2816 | 55 | 2871 | 2816 | 55 | 1 | False | True | exact_append | 2779 | 100.00% | 1789 | 1 / - |
| - | 6 | 2955 | 2816 | 139 | 2955 | 2816 | 139 | 1 | False | True | exact_append | 2871 | 98.08% | 3748 | 1 / - |
| - | 7 | 3051 | 2816 | 235 | 3051 | 2816 | 235 | 1 | False | True | exact_append | 2955 | 95.30% | 27208 | 1 / - |
| - | 8 | 3145 | 2816 | 329 | 3145 | 2816 | 329 | 1 | False | True | exact_append | 3051 | 92.30% | 7279 | 1 / - |
| - | 9 | 3252 | 3072 | 180 | 3252 | 3072 | 180 | 1 | False | True | exact_append | 3145 | 97.68% | 2356 | 1 / - |
| - | 10 | 3336 | 3072 | 264 | 3336 | 3072 | 264 | 1 | False | True | exact_append | 3252 | 94.46% | 16441 | 1 / - |
| - | 11 | 3432 | 3328 | 104 | 3432 | 3328 | 104 | 1 | False | True | exact_append | 3336 | 99.76% | 22741 | 1 / - |
| - | 12 | 3526 | 3328 | 198 | 3526 | 3328 | 198 | 1 | False | True | exact_append | 3432 | 96.97% | 6670 | 1 / - |

Cache model: steady basis `configured_warmup_requests`; theoretical input/output = 32690 / 33664 tokens; capture input/output = 96.96% / 94.16%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 31744 cached input + 4472 uncached input + 12216 output.

</details>

<details><summary>C02_fix_failing_test P1 (15 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C02_fix_failing_test-r1-20260802-085153-35965e8c | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3691 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085153-35965e8c | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1323 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085153-35965e8c | 3 | 860 | 512 | 348 | 860 | 512 | 348 | 1 | False | True | exact_append | 754 | 67.90% | 2092 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085153-35965e8c | 4 | 1041 | 768 | 273 | 1041 | 768 | 273 | 1 | False | True | exact_append | 860 | 89.30% | 3388 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085153-35965e8c | 5 | 1228 | 1024 | 204 | 1228 | 1024 | 204 | 1 | False | True | exact_append | 1041 | 98.37% | 2273 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-085322-1ebc5f73 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 7983 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-085322-1ebc5f73 | 2 | 722 | 0 | 722 | 722 | 0 | 722 | 1 | False | True | exact_append | 549 | 0.00% | 2090 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-085322-1ebc5f73 | 3 | 904 | 512 | 392 | 904 | 512 | 392 | 1 | False | True | exact_append | 722 | 70.91% | 2367 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-085322-1ebc5f73 | 4 | 1095 | 0 | 1095 | 1095 | 0 | 1095 | 1 | False | True | exact_append | 904 | 0.00% | 2304 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 2224 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 2 | 754 | 512 | 242 | 754 | 512 | 242 | 1 | False | True | exact_append | 549 | 93.26% | 1462 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 3 | 860 | 512 | 348 | 860 | 512 | 348 | 1 | False | True | exact_append | 754 | 67.90% | 1236 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 4 | 1041 | 768 | 273 | 1041 | 768 | 273 | 1 | False | True | exact_append | 860 | 89.30% | 2734 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 5 | 1383 | 1024 | 359 | 1383 | 1024 | 359 | 1 | False | True | exact_append | 1041 | 98.37% | 2437 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-085420-abedf083 | 6 | 1570 | 1280 | 290 | 1570 | 1280 | 290 | 1 | False | True | exact_append | 1383 | 92.55% | 2130 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 9966 / 11330 tokens; capture input/output = 69.36% / 61.01%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 6912 cached input + 6947 uncached input + 1742 output.

</details>

<details><summary>C02_fix_failing_test P2 (15 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C02_fix_failing_test-r1-20260802-085859-9da8a3df | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 7771 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085859-9da8a3df | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 3471 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085859-9da8a3df | 3 | 1017 | 512 | 505 | 1017 | 512 | 505 | 1 | False | True | exact_append | 754 | 67.90% | 1602 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085859-9da8a3df | 4 | 1198 | 768 | 430 | 1198 | 768 | 430 | 1 | False | True | exact_append | 1017 | 75.52% | 2811 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-085859-9da8a3df | 5 | 1384 | 1024 | 360 | 1384 | 1024 | 360 | 1 | False | True | exact_append | 1198 | 85.48% | 2345 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090011-c5cfcee8 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 10556 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090011-c5cfcee8 | 2 | 722 | 0 | 722 | 722 | 0 | 722 | 1 | False | True | exact_append | 549 | 0.00% | 3773 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090011-c5cfcee8 | 3 | 904 | 0 | 904 | 904 | 0 | 904 | 1 | False | True | exact_append | 722 | 0.00% | 4519 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090011-c5cfcee8 | 4 | 1095 | 0 | 1095 | 1095 | 0 | 1095 | 1 | False | True | exact_append | 904 | 0.00% | 6272 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3105 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 2 | 754 | 512 | 242 | 754 | 512 | 242 | 1 | False | True | exact_append | 549 | 93.26% | 2056 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 3 | 1017 | 0 | 1017 | 1017 | 0 | 1017 | 1 | False | True | exact_append | 754 | 0.00% | 2480 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 4 | 1198 | 768 | 430 | 1198 | 768 | 430 | 1 | False | True | exact_append | 1017 | 75.52% | 1672 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 5 | 1426 | 0 | 1426 | 1426 | 0 | 1426 | 1 | False | True | exact_append | 1198 | 0.00% | 2561 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090133-ea3e2317 | 6 | 1612 | 1280 | 332 | 1612 | 1280 | 332 | 1 | False | True | exact_append | 1426 | 89.76% | 3526 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 10637 / 11942 tokens; capture input/output = 45.73% / 40.73%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 4864 cached input + 9864 uncached input + 1686 output.

</details>

<details><summary>C07_large_log_debugging P1 (27 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 5963 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 2 | 1837 | 0 | 1837 | 1837 | 0 | 1837 | 1 | False | True | exact_append | 1201 | 0.00% | 8795 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 3 | 1961 | 1792 | 169 | 1961 | 1792 | 169 | 1 | False | True | exact_append | 1837 | 97.55% | 1847 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 4 | 2343 | 1792 | 551 | 2343 | 1792 | 551 | 1 | False | True | exact_append | 1961 | 91.38% | 10451 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 5 | 2786 | 1792 | 994 | 2786 | 1792 | 994 | 1 | False | True | exact_append | 2343 | 76.48% | 1462 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 6 | 2953 | 2560 | 393 | 2953 | 2560 | 393 | 1 | False | True | exact_append | 2786 | 91.89% | 14793 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 7 | 3198 | 2816 | 382 | 3198 | 2816 | 382 | 1 | False | True | exact_append | 2953 | 95.36% | 3139 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 8 | 2807 | 1024 | 1783 | 2807 | 1024 | 1783 | 2 | False | False | recent_window_moved | 1188 | 86.20% | 2081 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085219-836da13e | 9 | 2879 | 0 | 2879 | 2879 | 0 | 2879 | 3 | False | False | recent_window_moved | 1158 | 0.00% | 7601 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 5043 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 3289 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 3 | 1963 | 1792 | 171 | 1963 | 1792 | 171 | 1 | False | True | exact_append | 1839 | 97.44% | 2100 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 4 | 2345 | 1792 | 553 | 2345 | 1792 | 553 | 1 | False | True | exact_append | 1963 | 91.29% | 1392 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 5 | 2785 | 2304 | 481 | 2785 | 2304 | 481 | 1 | False | True | exact_append | 2345 | 98.25% | 1395 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 6 | 2951 | 2560 | 391 | 2951 | 2560 | 391 | 1 | False | True | exact_append | 2785 | 91.92% | 7872 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 7 | 3201 | 2816 | 385 | 3201 | 2816 | 385 | 1 | False | True | exact_append | 2951 | 95.43% | 1846 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 8 | 2808 | 1024 | 1784 | 2808 | 1024 | 1784 | 2 | False | False | recent_window_moved | 1191 | 85.98% | 2092 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-085342-9460cef8 | 9 | 2880 | 0 | 2880 | 2880 | 0 | 2880 | 3 | False | False | recent_window_moved | 1160 | 0.00% | 5566 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 5580 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 2 | 1839 | 1024 | 815 | 1839 | 1024 | 815 | 1 | False | True | exact_append | 1201 | 85.26% | 2382 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 3 | 1963 | 1792 | 171 | 1963 | 1792 | 171 | 1 | False | True | exact_append | 1839 | 97.44% | 1838 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 4 | 2345 | 1792 | 553 | 2345 | 1792 | 553 | 1 | False | True | exact_append | 1963 | 91.29% | 1635 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 5 | 2785 | 2304 | 481 | 2785 | 2304 | 481 | 1 | False | True | exact_append | 2345 | 98.25% | 1399 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 6 | 2951 | 2560 | 391 | 2951 | 2560 | 391 | 1 | False | True | exact_append | 2785 | 91.92% | 6631 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 7 | 3189 | 2816 | 373 | 3189 | 2816 | 373 | 1 | False | True | exact_append | 2951 | 95.43% | 2608 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 8 | 2794 | 0 | 2794 | 2794 | 0 | 2794 | 2 | False | False | recent_window_moved | 1188 | 0.00% | 1644 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-085438-ddac8f5c | 9 | 2866 | 0 | 2866 | 2866 | 0 | 2866 | 3 | False | False | recent_window_moved | 1158 | 0.00% | 5580 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 46292 / 49499 tokens; capture input/output = 78.53% / 73.44%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 36352 cached input + 29519 uncached input + 6414 output.

</details>

<details><summary>C07_large_log_debugging P2 (27 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 1 | 1201 | 256 | 945 | 1201 | 256 | 945 | 1 | True | False | cold_start | 0 | n/a | 4816 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 5081 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 3 | 1964 | 1792 | 172 | 1964 | 1792 | 172 | 1 | False | True | exact_append | 1839 | 97.44% | 2298 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 4 | 2346 | 1792 | 554 | 2346 | 1792 | 554 | 1 | False | True | exact_append | 1964 | 91.24% | 3371 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 5 | 2788 | 2304 | 484 | 2788 | 2304 | 484 | 1 | False | True | exact_append | 2346 | 98.21% | 3050 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 6 | 2955 | 2560 | 395 | 2955 | 2560 | 395 | 1 | False | True | exact_append | 2788 | 91.82% | 8791 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 7 | 3193 | 2816 | 377 | 3193 | 2816 | 377 | 1 | False | True | exact_append | 2955 | 95.30% | 1840 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 8 | 3438 | 3072 | 366 | 3438 | 3072 | 366 | 1 | False | True | exact_append | 3193 | 96.21% | 1298 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-085922-139f79b6 | 9 | 3634 | 3328 | 306 | 3634 | 3328 | 306 | 1 | False | True | exact_append | 3438 | 96.80% | 11673 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 10268 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 2254 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 3 | 1963 | 1792 | 171 | 1963 | 1792 | 171 | 1 | False | True | exact_append | 1839 | 97.44% | 4512 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 4 | 2345 | 1792 | 553 | 2345 | 1792 | 553 | 1 | False | True | exact_append | 1963 | 91.29% | 2193 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 5 | 2785 | 2304 | 481 | 2785 | 2304 | 481 | 1 | False | True | exact_append | 2345 | 98.25% | 4069 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 6 | 2951 | 2560 | 391 | 2951 | 2560 | 391 | 1 | False | True | exact_append | 2785 | 91.92% | 8747 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 7 | 3201 | 2816 | 385 | 3201 | 2816 | 385 | 1 | False | True | exact_append | 2951 | 95.43% | 1537 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 8 | 3446 | 3072 | 374 | 3446 | 3072 | 374 | 1 | False | True | exact_append | 3201 | 95.97% | 1262 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090042-9dff2dad | 9 | 3642 | 3328 | 314 | 3642 | 3328 | 314 | 1 | False | True | exact_append | 3446 | 96.58% | 9170 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 8142 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 4454 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 3 | 1964 | 1792 | 172 | 1964 | 1792 | 172 | 1 | False | True | exact_append | 1839 | 97.44% | 2577 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 4 | 2346 | 1792 | 554 | 2346 | 1792 | 554 | 1 | False | True | exact_append | 1964 | 91.24% | 7092 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 5 | 2788 | 2304 | 484 | 2788 | 2304 | 484 | 1 | False | True | exact_append | 2346 | 98.21% | 1413 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 6 | 2959 | 2560 | 399 | 2959 | 2560 | 399 | 1 | False | True | exact_append | 2788 | 91.82% | 13870 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 7 | 3217 | 2816 | 401 | 3217 | 2816 | 401 | 1 | False | True | exact_append | 2959 | 95.17% | 1888 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 8 | 3463 | 3072 | 391 | 3463 | 3072 | 391 | 1 | False | True | exact_append | 3217 | 95.49% | 1442 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090155-48ed6aca | 9 | 3659 | 3328 | 331 | 3659 | 3328 | 331 | 1 | False | True | exact_append | 3463 | 96.10% | 10234 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 59232 / 63602 tokens; capture input/output = 89.47% / 83.32%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 53248 cached input + 16919 uncached input + 7789 output.

</details>

## Round 2: PASS

- Namespace/order: `formal-v212-round-82` / `p2-first`
- P1/P2 probe: `cache-probe-20260802-091158-41f77517` / `cache-probe-20260802-090450-f639bff3`
- P1/P2 suite: `suite-20260802-091216-4b21d464` / `suite-20260802-090509-bfc41c2b`

| Workload | Variant | Requests | Prompt | Hit | Miss | Full-chain | Steady | Capture | Task pass | Prefix resets | Provider latency | E2E wall |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed-long | P1 | 12 | 35042 | 28416 | 6626 | 81.09% | 78.84% | 96.29% | 100.00% | 5 | 126805 ms | 128267 ms |
| fixed-long | P2 | 12 | 36216 | 30720 | 5496 | 84.82% | 91.53% | 93.97% | 100.00% | 0 | 91390 ms | 92863 ms |
| C02_fix_failing_test | P1 | 16 | 15725 | 4608 | 11117 | 29.30% | 42.67% | 40.05% | 100.00% | 0 | 38587 ms | 43641 ms |
| C02_fix_failing_test | P2 | 17 | 17534 | 6912 | 10622 | 39.42% | 62.01% | 52.82% | 100.00% | 0 | 47734 ms | 52890 ms |
| C07_large_log_debugging | P1 | 27 | 65910 | 37120 | 28790 | 56.32% | 62.03% | 79.72% | 100.00% | 6 | 129019 ms | 137667 ms |
| C07_large_log_debugging | P2 | 27 | 69883 | 52224 | 17659 | 74.73% | 81.84% | 86.76% | 100.00% | 0 | 128529 ms | 136877 ms |

### Gate detail

- PASS `comparable_configuration`
- PASS `shared_round_namespace`
- PASS `shared_execution_order`
- PASS `execution_order_verified`
- PASS `formal_clean_evidence`
- PASS `fixed_request_count`
- PASS `fixed_payloads_verified`
- PASS `fixed_metrics_complete`
- PASS `fixed_requests_and_tasks_passed`
- PASS `real_case_matrix_complete`
- PASS `suite_top_level_passed`
- PASS `no_extra_suite_cases`
- PASS `case_authority_profiles_locked`
- PASS `runtime_model_identity_verified`
- PASS `all_tasks_passed`
- PASS `all_cache_metrics_complete`
- PASS `provider_retries_within_budget`
- PASS `p2_fixed_full_chain_at_least_70`
- PASS `p2_long_full_chain_at_least_70`
- PASS `p2_fixed_steady_at_least_80`
- PASS `p2_long_steady_at_least_80`
- PASS `p2_fixed_capture_at_least_85`
- PASS `p2_long_capture_at_least_85`
- PASS `p2_theoretical_full_chain_qualifies`
- PASS `capture_efficiency_bounded`
- PASS `fixed_miss_improvement_or_saturation_target`
- PASS `long_post_slide_miss_reduction_at_least_40`
- PASS `fixed_prompt_inflation_within_10`
- PASS `long_prompt_inflation_within_10`
- PASS `prefix_accounting_complete`
- PASS `retry_cache_penalty_accounted`
- PASS `request_detail_complete`
- PASS `request_aggregates_reconcile`
- PASS `long_tasks_use_exactly_9_requests`
- PASS `long_action_shape_verified`
- PASS `long_post_slide_shape_comparable`
- PASS `p2_key_fact_retention_complete`

### Per-request evidence

<details><summary>fixed-long P1 (12 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| - | 1 | 2550 | 2304 | 246 | 2550 | 2304 | 246 | 1 | True | False | cold_start | 0 | n/a | 31108 | 1 / - |
| - | 2 | 2619 | 2560 | 59 | 2619 | 2560 | 59 | 1 | False | True | exact_append | 2550 | 100.00% | 20837 | 1 / - |
| - | 3 | 2700 | 2560 | 140 | 2700 | 2560 | 140 | 1 | False | True | exact_append | 2619 | 97.75% | 10032 | 1 / - |
| - | 4 | 2779 | 2560 | 219 | 2779 | 2560 | 219 | 1 | False | True | exact_append | 2700 | 94.81% | 4160 | 1 / - |
| - | 5 | 2871 | 2560 | 311 | 2871 | 2560 | 311 | 1 | False | True | exact_append | 2779 | 92.12% | 2659 | 1 / - |
| - | 6 | 2955 | 2816 | 139 | 2955 | 2816 | 139 | 1 | False | True | exact_append | 2871 | 98.08% | 24320 | 1 / - |
| - | 7 | 3051 | 2816 | 235 | 3051 | 2816 | 235 | 1 | False | True | exact_append | 2955 | 95.30% | 5268 | 1 / - |
| - | 8 | 3076 | 0 | 3076 | 3076 | 0 | 3076 | 2 | False | False | recent_window_moved | 0 | n/a | 9151 | 1 / - |
| - | 9 | 3102 | 2560 | 542 | 3102 | 2560 | 542 | 3 | False | False | recent_window_moved | 0 | n/a | 4898 | 1 / - |
| - | 10 | 3107 | 2560 | 547 | 3107 | 2560 | 547 | 4 | False | False | recent_window_moved | 0 | n/a | 6943 | 1 / - |
| - | 11 | 3111 | 2560 | 551 | 3111 | 2560 | 551 | 5 | False | False | recent_window_moved | 0 | n/a | 4881 | 1 / - |
| - | 12 | 3121 | 2560 | 561 | 3121 | 2560 | 561 | 6 | False | False | recent_window_moved | 0 | n/a | 2548 | 1 / - |

Cache model: steady basis `configured_warmup_requests`; theoretical input/output = 16474 / 16973 tokens; capture input/output = 96.29% / 93.45%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 28416 cached input + 6626 uncached input + 8371 output.

</details>

<details><summary>fixed-long P2 (12 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| - | 1 | 2550 | 0 | 2550 | 2550 | 0 | 2550 | 1 | True | False | cold_start | 0 | n/a | 20303 | 1 / - |
| - | 2 | 2619 | 2304 | 315 | 2619 | 2304 | 315 | 1 | False | True | exact_append | 2550 | 90.35% | 13041 | 1 / - |
| - | 3 | 2700 | 2560 | 140 | 2700 | 2560 | 140 | 1 | False | True | exact_append | 2619 | 97.75% | 9171 | 1 / - |
| - | 4 | 2779 | 2560 | 219 | 2779 | 2560 | 219 | 1 | False | True | exact_append | 2700 | 94.81% | 8057 | 1 / - |
| - | 5 | 2871 | 2560 | 311 | 2871 | 2560 | 311 | 1 | False | True | exact_append | 2779 | 92.12% | 2805 | 1 / - |
| - | 6 | 2955 | 2560 | 395 | 2955 | 2560 | 395 | 1 | False | True | exact_append | 2871 | 89.17% | 5326 | 1 / - |
| - | 7 | 3051 | 2816 | 235 | 3051 | 2816 | 235 | 1 | False | True | exact_append | 2955 | 95.30% | 4542 | 1 / - |
| - | 8 | 3145 | 2816 | 329 | 3145 | 2816 | 329 | 1 | False | True | exact_append | 3051 | 92.30% | 3554 | 1 / - |
| - | 9 | 3252 | 2816 | 436 | 3252 | 2816 | 436 | 1 | False | True | exact_append | 3145 | 89.54% | 3502 | 1 / - |
| - | 10 | 3336 | 3072 | 264 | 3336 | 3072 | 264 | 1 | False | True | exact_append | 3252 | 94.46% | 9000 | 1 / - |
| - | 11 | 3432 | 3328 | 104 | 3432 | 3328 | 104 | 1 | False | True | exact_append | 3336 | 99.76% | 7455 | 1 / - |
| - | 12 | 3526 | 3328 | 198 | 3526 | 3328 | 198 | 1 | False | True | exact_append | 3432 | 96.97% | 4634 | 1 / - |

Cache model: steady basis `configured_warmup_requests`; theoretical input/output = 32690 / 33666 tokens; capture input/output = 93.97% / 91.25%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 30720 cached input + 5496 uncached input + 5250 output.

</details>

<details><summary>C02_fix_failing_test P1 (16 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C02_fix_failing_test-r1-20260802-091216-174c3ea3 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3273 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-091216-174c3ea3 | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1250 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-091216-174c3ea3 | 3 | 1017 | 0 | 1017 | 1017 | 0 | 1017 | 1 | False | True | exact_append | 754 | 0.00% | 2180 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-091216-174c3ea3 | 4 | 1198 | 512 | 686 | 1198 | 512 | 686 | 1 | False | True | exact_append | 1017 | 50.34% | 4467 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-091216-174c3ea3 | 5 | 1385 | 0 | 1385 | 1385 | 0 | 1385 | 1 | False | True | exact_append | 1198 | 0.00% | 2614 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-091330-25d00fd0 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3396 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-091330-25d00fd0 | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1860 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-091330-25d00fd0 | 3 | 1017 | 512 | 505 | 1017 | 512 | 505 | 1 | False | True | exact_append | 754 | 67.90% | 1428 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-091330-25d00fd0 | 4 | 1198 | 768 | 430 | 1198 | 768 | 430 | 1 | False | True | exact_append | 1017 | 75.52% | 2903 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-091330-25d00fd0 | 5 | 1384 | 512 | 872 | 1384 | 512 | 872 | 1 | False | True | exact_append | 1198 | 42.74% | 2053 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 2342 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1895 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 3 | 860 | 512 | 348 | 860 | 512 | 348 | 1 | False | True | exact_append | 754 | 67.90% | 1979 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 4 | 1041 | 768 | 273 | 1041 | 768 | 273 | 1 | False | True | exact_append | 860 | 89.30% | 2574 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 5 | 1265 | 1024 | 241 | 1265 | 1024 | 241 | 1 | False | True | exact_append | 1041 | 98.37% | 2293 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-091440-2a9fe152 | 6 | 1451 | 0 | 1451 | 1451 | 0 | 1451 | 1 | False | True | exact_append | 1265 | 0.00% | 2080 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 11505 / 12911 tokens; capture input/output = 40.05% / 35.69%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 4608 cached input + 11117 uncached input + 1757 output.

</details>

<details><summary>C02_fix_failing_test P2 (17 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3169 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1733 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 3 | 1017 | 0 | 1017 | 1017 | 0 | 1017 | 1 | False | True | exact_append | 754 | 0.00% | 2125 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 4 | 1198 | 768 | 430 | 1198 | 768 | 430 | 1 | False | True | exact_append | 1017 | 75.52% | 3594 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 5 | 1426 | 512 | 914 | 1426 | 512 | 914 | 1 | False | True | exact_append | 1198 | 42.74% | 2442 | 1 / - |
| eval-C02_fix_failing_test-r1-20260802-090509-3d8218ca | 6 | 1613 | 1024 | 589 | 1613 | 1024 | 589 | 1 | False | True | exact_append | 1426 | 71.81% | 3742 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 2966 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 2 | 754 | 0 | 754 | 754 | 0 | 754 | 1 | False | True | exact_append | 549 | 0.00% | 1356 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 3 | 1017 | 0 | 1017 | 1017 | 0 | 1017 | 1 | False | True | exact_append | 754 | 0.00% | 2225 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 4 | 1198 | 0 | 1198 | 1198 | 0 | 1198 | 1 | False | True | exact_append | 1017 | 0.00% | 2957 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 5 | 1422 | 1024 | 398 | 1422 | 1024 | 398 | 1 | False | True | exact_append | 1198 | 85.48% | 3743 | 1 / - |
| eval-C02_fix_failing_test-r2-20260802-090621-0f6033e0 | 6 | 1608 | 1280 | 328 | 1608 | 1280 | 328 | 1 | False | True | exact_append | 1422 | 90.01% | 3235 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090737-0b883f03 | 1 | 549 | 0 | 549 | 549 | 0 | 549 | 1 | True | False | cold_start | 0 | n/a | 3066 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090737-0b883f03 | 2 | 754 | 512 | 242 | 754 | 512 | 242 | 1 | False | True | exact_append | 549 | 93.26% | 2757 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090737-0b883f03 | 3 | 859 | 0 | 859 | 859 | 0 | 859 | 1 | False | True | exact_append | 754 | 0.00% | 1617 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090737-0b883f03 | 4 | 1040 | 768 | 272 | 1040 | 768 | 272 | 1 | False | True | exact_append | 859 | 89.41% | 2068 | 1 / - |
| eval-C02_fix_failing_test-r3-20260802-090737-0b883f03 | 5 | 1227 | 1024 | 203 | 1227 | 1024 | 203 | 1 | False | True | exact_append | 1040 | 98.46% | 4939 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 13086 / 14546 tokens; capture input/output = 52.82% / 47.52%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 6912 cached input + 10622 uncached input + 1809 output.

</details>

<details><summary>C07_large_log_debugging P1 (27 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 5955 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 2 | 1837 | 0 | 1837 | 1837 | 0 | 1837 | 1 | False | True | exact_append | 1201 | 0.00% | 4094 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 3 | 1961 | 1792 | 169 | 1961 | 1792 | 169 | 1 | False | True | exact_append | 1837 | 97.55% | 4660 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 4 | 2343 | 1792 | 551 | 2343 | 1792 | 551 | 1 | False | True | exact_append | 1961 | 91.38% | 3363 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 5 | 2786 | 1792 | 994 | 2786 | 1792 | 994 | 1 | False | True | exact_append | 2343 | 76.48% | 2282 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 6 | 2953 | 2560 | 393 | 2953 | 2560 | 393 | 1 | False | True | exact_append | 2786 | 91.89% | 11436 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 7 | 3213 | 2816 | 397 | 3213 | 2816 | 397 | 1 | False | True | exact_append | 2953 | 95.36% | 4593 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 8 | 2823 | 0 | 2823 | 2823 | 0 | 2823 | 2 | False | False | recent_window_moved | 1189 | 0.00% | 2069 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-091236-bd267df5 | 9 | 2895 | 1024 | 1871 | 2895 | 1024 | 1871 | 3 | False | False | recent_window_moved | 1159 | 88.35% | 8470 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 5854 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 2 | 1832 | 0 | 1832 | 1832 | 0 | 1832 | 1 | False | True | exact_append | 1201 | 0.00% | 4347 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 3 | 1955 | 1792 | 163 | 1955 | 1792 | 163 | 1 | False | True | exact_append | 1832 | 97.82% | 1606 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 4 | 2331 | 1792 | 539 | 2331 | 1792 | 539 | 1 | False | True | exact_append | 1955 | 91.66% | 6819 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 5 | 2765 | 2304 | 461 | 2765 | 2304 | 461 | 1 | False | True | exact_append | 2331 | 98.84% | 1651 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 6 | 2926 | 2560 | 366 | 2926 | 2560 | 366 | 1 | False | True | exact_append | 2765 | 92.59% | 10778 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 7 | 3172 | 2816 | 356 | 3172 | 2816 | 356 | 1 | False | True | exact_append | 2926 | 96.24% | 2020 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 8 | 2782 | 1024 | 1758 | 2782 | 1024 | 1758 | 2 | False | False | recent_window_moved | 1192 | 85.91% | 4590 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-091347-25dc2fc7 | 9 | 2855 | 1024 | 1831 | 2855 | 1024 | 1831 | 3 | False | False | recent_window_moved | 1160 | 88.28% | 8125 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 1 | 1201 | 256 | 945 | 1201 | 256 | 945 | 1 | True | False | cold_start | 0 | n/a | 7164 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 5730 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 3 | 1964 | 1024 | 940 | 1964 | 1024 | 940 | 1 | False | True | exact_append | 1839 | 55.68% | 4504 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 4 | 2346 | 1792 | 554 | 2346 | 1792 | 554 | 1 | False | True | exact_append | 1964 | 91.24% | 1475 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 5 | 2789 | 2304 | 485 | 2789 | 2304 | 485 | 1 | False | True | exact_append | 2346 | 98.21% | 2114 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 6 | 2960 | 1792 | 1168 | 2960 | 1792 | 1168 | 1 | False | True | exact_append | 2789 | 64.25% | 4453 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 7 | 3229 | 2816 | 413 | 3229 | 2816 | 413 | 1 | False | True | exact_append | 2960 | 95.14% | 1695 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 8 | 2840 | 1024 | 1816 | 2840 | 1024 | 1816 | 2 | False | False | recent_window_moved | 1190 | 86.05% | 2366 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-091459-d6578d75 | 9 | 2911 | 1024 | 1887 | 2911 | 1024 | 1887 | 3 | False | False | recent_window_moved | 1160 | 88.28% | 6806 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 46240 / 49708 tokens; capture input/output = 79.72% / 74.16%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 37120 cached input + 28790 uncached input + 6972 output.

</details>

<details><summary>C07_large_log_debugging P2 (27 requests)</summary>

| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | Attempts/reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 6280 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 2 | 1832 | 0 | 1832 | 1832 | 0 | 1832 | 1 | False | True | exact_append | 1201 | 0.00% | 4093 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 3 | 1955 | 1792 | 163 | 1955 | 1792 | 163 | 1 | False | True | exact_append | 1832 | 97.82% | 2151 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 4 | 2331 | 1792 | 539 | 2331 | 1792 | 539 | 1 | False | True | exact_append | 1955 | 91.66% | 6736 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 5 | 2765 | 2304 | 461 | 2765 | 2304 | 461 | 1 | False | True | exact_append | 2331 | 98.84% | 1343 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 6 | 2926 | 2560 | 366 | 2926 | 2560 | 366 | 1 | False | True | exact_append | 2765 | 92.59% | 11058 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 7 | 3161 | 2816 | 345 | 3161 | 2816 | 345 | 1 | False | True | exact_append | 2926 | 96.24% | 2757 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 8 | 3402 | 3072 | 330 | 3402 | 3072 | 330 | 1 | False | True | exact_append | 3161 | 97.18% | 1504 | 1 / - |
| eval-C07_large_log_debugging-r1-20260802-090531-f7397c07 | 9 | 3598 | 3328 | 270 | 3598 | 3328 | 270 | 1 | False | True | exact_append | 3402 | 97.82% | 6745 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 1 | 1201 | 0 | 1201 | 1201 | 0 | 1201 | 1 | True | False | cold_start | 0 | n/a | 11416 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 2725 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 3 | 1963 | 1792 | 171 | 1963 | 1792 | 171 | 1 | False | True | exact_append | 1839 | 97.44% | 3292 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 4 | 2345 | 1792 | 553 | 2345 | 1792 | 553 | 1 | False | True | exact_append | 1963 | 91.29% | 2880 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 5 | 2785 | 2304 | 481 | 2785 | 2304 | 481 | 1 | False | True | exact_append | 2345 | 98.25% | 2610 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 6 | 2951 | 1792 | 1159 | 2951 | 1792 | 1159 | 1 | False | True | exact_append | 2785 | 64.34% | 11325 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 7 | 3190 | 2816 | 374 | 3190 | 2816 | 374 | 1 | False | True | exact_append | 2951 | 95.43% | 2498 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 8 | 3433 | 3072 | 361 | 3433 | 3072 | 361 | 1 | False | True | exact_append | 3190 | 96.30% | 1860 | 1 / - |
| eval-C07_large_log_debugging-r2-20260802-090644-725aeed8 | 9 | 3629 | 3328 | 301 | 3629 | 3328 | 301 | 1 | False | True | exact_append | 3433 | 96.94% | 7306 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 1 | 1201 | 1024 | 177 | 1201 | 1024 | 177 | 1 | True | False | cold_start | 0 | n/a | 5614 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 2 | 1839 | 0 | 1839 | 1839 | 0 | 1839 | 1 | False | True | exact_append | 1201 | 0.00% | 3253 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 3 | 1964 | 1024 | 940 | 1964 | 1024 | 940 | 1 | False | True | exact_append | 1839 | 55.68% | 2254 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 4 | 2346 | 1792 | 554 | 2346 | 1792 | 554 | 1 | False | True | exact_append | 1964 | 91.24% | 3793 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 5 | 2788 | 2304 | 484 | 2788 | 2304 | 484 | 1 | False | True | exact_append | 2346 | 98.21% | 2499 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 6 | 2955 | 2560 | 395 | 2955 | 2560 | 395 | 1 | False | True | exact_append | 2788 | 91.82% | 8675 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 7 | 3199 | 2816 | 383 | 3199 | 2816 | 383 | 1 | False | True | exact_append | 2955 | 95.30% | 4859 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 8 | 3444 | 3072 | 372 | 3444 | 3072 | 372 | 1 | False | True | exact_append | 3199 | 96.03% | 2066 | 1 / - |
| eval-C07_large_log_debugging-r3-20260802-090757-60234dc6 | 9 | 3640 | 3072 | 568 | 3640 | 3072 | 568 | 1 | False | True | exact_append | 3444 | 89.20% | 6937 | 1 / - |

Cache model: steady basis `first_observed_cache_hit`; theoretical input/output = 59016 / 63122 tokens; capture input/output = 86.76% / 81.11%; empirical hit block = 256 tokens.

Retry accounting: 0 logical requests retried; 0 upper-bound physical input tokens added; 0 raw hit tokens moved to miss. Each retried request is conservatively costed as attempt_count × final prompt with zero effective hit; later requests remain measured normally.

Cost estimate: monetary amount unavailable because no immutable Provider price contract was configured; token basis = 52224 cached input + 17659 uncached input + 7175 output.

</details>

## Global criteria

- PASS `exactly_two_rounds`
- PASS `independent_evidence_ids`
- PASS `independent_run_ids`
- PASS `independent_sequence_ids`
- PASS `balanced_execution_order`
- PASS `execution_order_verified`
- PASS `sequence_shape_consistent`
- PASS `locked_configuration_consistent`
- PASS `case_authority_profiles_consistent`
- PASS `runtime_model_identity_verified`
- PASS `short_balanced_prompt_inflation_within_10`
- PASS `short_balanced_miss_inflation_within_15`
- PASS `all_rounds_passed`
