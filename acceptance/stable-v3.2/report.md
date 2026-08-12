# V3.2 Skill/Feedback Guidance A/B

Overall: **PASS**
Execution commit: `178d3ed142b1d492c741539685cb13b51aa075f0`
Verification commit: `c3ed297d9d66630fb4f726ecfb74f39b223ed2e6`

Goal-relevant Skill catalog and commit-bound Feedback rules are selected with exact precision on the canonical case without reducing task pass rate. Automatic feedback extraction, ambient retrieval, RAG, and cross-task quality uplift are not claimed.

## Arms

- A0 disabled: 3/3, suite `suite-20260812-145957-8735fe6b`
- A1 enabled: 3/3, suite `suite-20260812-150155-3b650c49`

## Criteria

- PASS `source_commit_present`
- PASS `source_commit_matches_suites`
- PASS `verification_commit_present`
- PASS `verification_delta_allowed`
- PASS `suite_integrity_verified`
- PASS `canonical_identical_case`
- PASS `minimum_attempts_each`
- PASS `independent_runs`
- PASS `comparable_configuration`
- PASS `variant_identity`
- PASS `shared_sequence_and_order`
- PASS `disabled_selects_nothing`
- PASS `enabled_selects_exact_relevant_guidance`
- PASS `enabled_selection_events_once`
- PASS `enabled_pass_rate_not_lower`
- PASS `enabled_all_pass`
- PASS `enabled_uses_fewer_bash_actions`
- PASS `enabled_uses_fewer_prompt_tokens`
- PASS `no_provider_or_protocol_failures`
