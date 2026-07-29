# miniCC V2.1.1 Prompt Cache P0/P1 A/B

Status: **PASS**
Independent rounds: 2/2
Unique immutable evidence: yes
Unique run evidence: yes
Unique round namespaces: yes
Balanced verified execution order: yes
Same-direction cache improvement: yes

## Round 1: PASS

- P0 fixed probe: `cache-probe-20260729-104505-7735d223`
- P1 fixed probe: `cache-probe-20260729-104412-344537da`
- P0 real-case suite: `suite-20260729-104729-dbd9b783`
- P1 real-case suite: `suite-20260729-104520-3f1672ea`
- Namespace/order: `round-19` / `p1-first` (verified)
- Stable prefix hash: P0 fixed=`1dbd83068ab96b53b9981d79c4f7d7b97064cbc6991db2e292e1b21db4695d04`, P1 fixed=`02276f1a4d2b2c308e3ebd7c0be563a91a9f82c47bad1f4a942b12328b5f8a4a`
- Stable prefix hash: P0 real=`1dbd83068ab96b53b9981d79c4f7d7b97064cbc6991db2e292e1b21db4695d04`, P1 real=`78da5283aa6b94d64149340dcb8e36fe6e936f45fdc5276734379525abab3e71`

| Workload | Variant | Requests | Reported | Unreported | Hit tokens | Miss tokens | Weighted hit rate | Prompt tokens | Latency total/mean ms | Task pass | Stable prefix est. tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed/all | P0 | 5 | 5 | 0 | 0 | 3554 | 0.00% | 3554 | 35056/7011.2 | 100.00% | 555 |
| fixed/all | P1 | 5 | 5 | 0 | 1024 | 2340 | 30.44% | 3364 | 18526/3705.2 | 100.00% | 678 |
| fixed/steady | P0 | 3 | 3 | 0 | 0 | 2409 | 0.00% | 2409 | 24683/8227.7 | 100.00% | 555 |
| fixed/steady | P1 | 3 | 3 | 0 | 1024 | 1233 | 45.37% | 2257 | 10422/3474.0 | 100.00% | 678 |
| real | P0 | 15 | 15 | 0 | 512 | 14897 | 3.32% | 15409 | 83921/5594.7 | 100.00% | 555 |
| real | P1 | 15 | 15 | 0 | 3072 | 10167 | 23.20% | 13239 | 88973/5931.5 | 100.00% | 711 |

Actual improvement: fixed_rate_delta=+45.37%, fixed_hit_delta=1024, real_rate_delta=+19.88%, real_hit_delta=2560, combined_rate_delta=+23.56%.

### Fixed probe request detail

| Variant | # | Request | Task | Attempts | Cache | Prompt | Hit | Miss | Hit rate | Latency ms | Request SHA-256 |
|---|---:|---|---|---:|---|---:|---:|---:|---:|---:|---|
| P0 | 1 | PASS | PASS | 1.0 | zero_hit | 519.0 | 0.0 | 519.0 | 0.00% | 4556.0 | `02276f1a4d2b` |
| P0 | 2 | PASS | PASS | 1.0 | zero_hit | 626.0 | 0.0 | 626.0 | 0.00% | 5817.0 | `15b8bfbbe621` |
| P0 | 3 | PASS | PASS | 1.0 | zero_hit | 720.0 | 0.0 | 720.0 | 0.00% | 9360.0 | `5386fb46d7ce` |
| P0 | 4 | PASS | PASS | 1.0 | zero_hit | 795.0 | 0.0 | 795.0 | 0.00% | 8759.0 | `4de874e551b9` |
| P0 | 5 | PASS | PASS | 1.0 | zero_hit | 894.0 | 0.0 | 894.0 | 0.00% | 6564.0 | `b4ebf3a9632e` |
| P1 | 1 | PASS | PASS | 1.0 | zero_hit | 519.0 | 0.0 | 519.0 | 0.00% | 5256.0 | `02276f1a4d2b` |
| P1 | 2 | PASS | PASS | 1.0 | zero_hit | 588.0 | 0.0 | 588.0 | 0.00% | 2848.0 | `f4a96822956a` |
| P1 | 3 | PASS | PASS | 1.0 | zero_hit | 669.0 | 0.0 | 669.0 | 0.00% | 6520.0 | `c3a8fc8c98e1` |
| P1 | 4 | PASS | PASS | 1.0 | nonzero_hit | 748.0 | 512.0 | 236.0 | 68.45% | 1494.0 | `935d25ca94ba` |
| P1 | 5 | PASS | PASS | 1.0 | nonzero_hit | 840.0 | 512.0 | 328.0 | 60.95% | 2408.0 | `980da2d497c3` |

### Real C02 run detail

| Variant | Attempt | Run ID | Pass | Task | Agent | Infra | Requests | Prompt | Hit | Miss | Latency ms | Provider attempts | Retries | Layout |
|---|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| P0 | 1 | `eval-C02_fix_failing_test-r1-20260729-104729-ab73711d` | PASS | PASS | PASS | PASS | 5.0 | 5350.0 | 256.0 | 5094.0 | 28197.0 | 5.0 | 0.0 | rebuild |
| P0 | 2 | `eval-C02_fix_failing_test-r2-20260729-104802-a7db4478` | PASS | PASS | PASS | PASS | 5.0 | 4709.0 | 256.0 | 4453.0 | 24491.0 | 5.0 | 0.0 | rebuild |
| P0 | 3 | `eval-C02_fix_failing_test-r3-20260729-104832-856cdf10` | PASS | PASS | PASS | PASS | 5.0 | 5350.0 | 0.0 | 5350.0 | 31233.0 | 5.0 | 0.0 | rebuild |
| P1 | 1 | `eval-C02_fix_failing_test-r1-20260729-104520-56e09f4d` | PASS | PASS | PASS | PASS | 5.0 | 4413.0 | 1024.0 | 3389.0 | 25689.0 | 5.0 | 0.0 | append |
| P1 | 2 | `eval-C02_fix_failing_test-r2-20260729-104551-e1826703` | PASS | PASS | PASS | PASS | 5.0 | 4413.0 | 1024.0 | 3389.0 | 39570.0 | 5.0 | 0.0 | append |
| P1 | 3 | `eval-C02_fix_failing_test-r3-20260729-104637-32ad769d` | PASS | PASS | PASS | PASS | 5.0 | 4413.0 | 1024.0 | 3389.0 | 23714.0 | 5.0 | 0.0 | append |

- PASS `comparable_configuration`
- PASS `formal_immutable_evidence`
- PASS `formal_locked_docker_runtime`
- PASS `fixed_sequence_exact_request_count`
- PASS `fixed_sequence_request_indices_locked`
- PASS `fixed_dynamic_sequence_verified`
- PASS `fixed_request_payloads_unique`
- PASS `fixed_request_payloads_verified`
- PASS `fixed_warmup_rule_locked`
- PASS `fixed_sequence_requests_succeeded`
- PASS `required_real_case_matrix`
- PASS `cache_metrics_complete`
- PASS `latency_metrics_complete`
- PASS `no_retried_provider_requests`
- PASS `p0_real_suite_all_passed`
- PASS `p1_real_suite_all_passed`
- PASS `p1_task_pass_rate_not_lower`
- PASS `p1_stable_prefix_not_lower`
- PASS `fixed_prompt_tokens_not_larger_per_request`
- PASS `real_prompt_token_metrics_complete`
- PASS `real_prompt_tokens_not_larger`
- PASS `fixed_cache_improved`
- PASS `fixed_uncached_tokens_lower`
- PASS `real_cache_not_lower`
- PASS `execution_order_verified`

## Round 2: PASS

- P0 fixed probe: `cache-probe-20260729-105020-7f42553f`
- P1 fixed probe: `cache-probe-20260729-105114-225433e9`
- P0 real-case suite: `suite-20260729-105135-616ea667`
- P1 real-case suite: `suite-20260729-105350-15a8e5ac`
- Namespace/order: `round-20` / `p0-first` (verified)
- Stable prefix hash: P0 fixed=`1dbd83068ab96b53b9981d79c4f7d7b97064cbc6991db2e292e1b21db4695d04`, P1 fixed=`796c14a4d8c3ba5b607c31d6a68b22df363de110a9dceee59d5e51ba8bac8f59`
- Stable prefix hash: P0 real=`1dbd83068ab96b53b9981d79c4f7d7b97064cbc6991db2e292e1b21db4695d04`, P1 real=`09f04df35992f833ae82d2196876bf2c5cf6e320eec921e9a10a1fd3bc43c3f4`

| Workload | Variant | Requests | Reported | Unreported | Hit tokens | Miss tokens | Weighted hit rate | Prompt tokens | Latency total/mean ms | Task pass | Stable prefix est. tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed/all | P0 | 5 | 5 | 0 | 0 | 3554 | 0.00% | 3554 | 38929/7785.8 | 100.00% | 555 |
| fixed/all | P1 | 5 | 5 | 0 | 512 | 2852 | 15.22% | 3364 | 33656/6731.2 | 100.00% | 678 |
| fixed/steady | P0 | 3 | 3 | 0 | 0 | 2409 | 0.00% | 2409 | 30321/10107.0 | 100.00% | 555 |
| fixed/steady | P1 | 3 | 3 | 0 | 512 | 1745 | 22.68% | 2257 | 25781/8593.7 | 100.00% | 678 |
| real | P0 | 15 | 15 | 0 | 512 | 14555 | 3.40% | 15067 | 90025/6001.7 | 100.00% | 555 |
| real | P1 | 14 | 14 | 0 | 3072 | 9494 | 24.45% | 12566 | 77474/5533.9 | 100.00% | 711 |

Actual improvement: fixed_rate_delta=+22.68%, fixed_hit_delta=512, real_rate_delta=+21.05%, real_hit_delta=2560, combined_rate_delta=+21.25%.

### Fixed probe request detail

| Variant | # | Request | Task | Attempts | Cache | Prompt | Hit | Miss | Hit rate | Latency ms | Request SHA-256 |
|---|---:|---|---|---:|---|---:|---:|---:|---:|---:|---|
| P0 | 1 | PASS | PASS | 1.0 | zero_hit | 519.0 | 0.0 | 519.0 | 0.00% | 4352.0 | `796c14a4d8c3` |
| P0 | 2 | PASS | PASS | 1.0 | zero_hit | 626.0 | 0.0 | 626.0 | 0.00% | 4256.0 | `a26e9c54324b` |
| P0 | 3 | PASS | PASS | 1.0 | zero_hit | 720.0 | 0.0 | 720.0 | 0.00% | 9306.0 | `e340505253ac` |
| P0 | 4 | PASS | PASS | 1.0 | zero_hit | 795.0 | 0.0 | 795.0 | 0.00% | 10009.0 | `48126148548d` |
| P0 | 5 | PASS | PASS | 1.0 | zero_hit | 894.0 | 0.0 | 894.0 | 0.00% | 11006.0 | `1f04b652f0dd` |
| P1 | 1 | PASS | PASS | 1.0 | zero_hit | 519.0 | 0.0 | 519.0 | 0.00% | 5106.0 | `796c14a4d8c3` |
| P1 | 2 | PASS | PASS | 1.0 | zero_hit | 588.0 | 0.0 | 588.0 | 0.00% | 2769.0 | `4dba7b91d68d` |
| P1 | 3 | PASS | PASS | 1.0 | nonzero_hit | 669.0 | 512.0 | 157.0 | 76.53% | 3892.0 | `4ef0ac71b6f2` |
| P1 | 4 | PASS | PASS | 1.0 | zero_hit | 748.0 | 0.0 | 748.0 | 0.00% | 4180.0 | `8bb5b717e481` |
| P1 | 5 | PASS | PASS | 1.0 | zero_hit | 840.0 | 0.0 | 840.0 | 0.00% | 17709.0 | `7830f18ce24f` |

### Real C02 run detail

| Variant | Attempt | Run ID | Pass | Task | Agent | Infra | Requests | Prompt | Hit | Miss | Latency ms | Provider attempts | Retries | Layout |
|---|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| P0 | 1 | `eval-C02_fix_failing_test-r1-20260729-105135-0904b406` | PASS | PASS | PASS | PASS | 5.0 | 5354.0 | 0.0 | 5354.0 | 37620.0 | 5.0 | 0.0 | rebuild |
| P0 | 2 | `eval-C02_fix_failing_test-r2-20260729-105218-d8af28b1` | PASS | PASS | PASS | PASS | 5.0 | 4995.0 | 0.0 | 4995.0 | 25786.0 | 5.0 | 0.0 | rebuild |
| P0 | 3 | `eval-C02_fix_failing_test-r3-20260729-105249-802244da` | PASS | PASS | PASS | PASS | 5.0 | 4718.0 | 512.0 | 4206.0 | 26619.0 | 5.0 | 0.0 | rebuild |
| P1 | 1 | `eval-C02_fix_failing_test-r1-20260729-105350-ee2c8e35` | PASS | PASS | PASS | PASS | 5.0 | 4413.0 | 2304.0 | 2109.0 | 13038.0 | 5.0 | 0.0 | append |
| P1 | 2 | `eval-C02_fix_failing_test-r2-20260729-105409-97734e4f` | PASS | PASS | PASS | PASS | 5.0 | 4887.0 | 768.0 | 4119.0 | 22691.0 | 5.0 | 0.0 | append |
| P1 | 3 | `eval-C02_fix_failing_test-r3-20260729-105437-1f30fe81` | PASS | PASS | PASS | PASS | 4.0 | 3266.0 | 0.0 | 3266.0 | 41745.0 | 4.0 | 0.0 | append |

- PASS `comparable_configuration`
- PASS `formal_immutable_evidence`
- PASS `formal_locked_docker_runtime`
- PASS `fixed_sequence_exact_request_count`
- PASS `fixed_sequence_request_indices_locked`
- PASS `fixed_dynamic_sequence_verified`
- PASS `fixed_request_payloads_unique`
- PASS `fixed_request_payloads_verified`
- PASS `fixed_warmup_rule_locked`
- PASS `fixed_sequence_requests_succeeded`
- PASS `required_real_case_matrix`
- PASS `cache_metrics_complete`
- PASS `latency_metrics_complete`
- PASS `no_retried_provider_requests`
- PASS `p0_real_suite_all_passed`
- PASS `p1_real_suite_all_passed`
- PASS `p1_task_pass_rate_not_lower`
- PASS `p1_stable_prefix_not_lower`
- PASS `fixed_prompt_tokens_not_larger_per_request`
- PASS `real_prompt_token_metrics_complete`
- PASS `real_prompt_tokens_not_larger`
- PASS `fixed_cache_improved`
- PASS `fixed_uncached_tokens_lower`
- PASS `real_cache_not_lower`
- PASS `execution_order_verified`
