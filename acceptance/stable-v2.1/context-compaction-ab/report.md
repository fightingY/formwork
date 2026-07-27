# miniCC V2.1 Context Compaction A/B

Status: **PASS**
Independent rounds: 2/2
Unique suite evidence: yes

## Round 1: PASS

- A0 suite: `suite-20260721-114710-5f07d43c`; pass rate=1.000; prompt mean/max/n=3317.8/4547/16
- A1 suite: `suite-20260721-114932-d19d9a3c`; pass rate=1.000; prompt mean/max/n=3010.4/3907/20
- Prompt reduction: 9.27%
- Retention: A0=100.00%, A1=100.00%
- Repeated I/O mean: A0=0.00, A1=0.33
- Cache: A0=supported, A1=supported
- A1 compaction overhead: prompt_tokens=7289, completion_tokens=14808, latency_ms=318370
- PASS `comparable_configuration`
- PASS `a0_budget_triggered_in_every_run`
- PASS `a1_semantic_compaction_triggered_in_every_run`
- PASS `a1_pass_rate_not_lower`
- PASS `a1_case_pass_rates_not_lower`
- PASS `a1_prompt_mean_lower`
- PASS `critical_fact_retention_100_percent`
- PASS `repeated_io_not_significantly_higher`

## Round 2: PASS

- A0 suite: `suite-20260727-v21-round2-a0-release-caebc1c`; pass rate=1.000; prompt mean/max/n=10599.9/43105/69
- A1 suite: `suite-20260727-v21-round2-a1-release-caebc1c`; pass rate=1.000; prompt mean/max/n=5660.7/29185/71
- Prompt reduction: 46.60%
- Retention: A0=100.00%, A1=100.00%
- Repeated I/O mean: A0=0.11, A1=1.11
- Cache: A0=supported, A1=supported
- A1 compaction overhead: prompt_tokens=49024, completion_tokens=87742, latency_ms=4313909
- PASS `comparable_configuration`
- PASS `a0_budget_triggered_in_every_run`
- PASS `a1_semantic_compaction_triggered_in_every_run`
- PASS `a1_pass_rate_not_lower`
- PASS `a1_case_pass_rates_not_lower`
- PASS `a1_prompt_mean_lower`
- PASS `critical_fact_retention_100_percent`
- PASS `repeated_io_not_significantly_higher`
