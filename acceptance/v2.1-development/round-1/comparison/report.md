# miniCC V2.1 Context Compaction A/B

Status: **INCONCLUSIVE**
Independent rounds: 1/2
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
