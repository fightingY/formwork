# Stable V3.5 archive

This archive contains the frozen public benchmark contract, deterministic
checkpoint recovery evidence, and the Context A/B reporting contract.

The benchmark and Context A/B folders are intentionally marked `not_run` in
this checkout: a formal 18-run Docker/DeepSeek execution requires the local
provider credentials and pinned Docker image. No synthetic model results are
represented as acceptance data. The deterministic recovery matrix is fully
reproducible from `tools/run_v35_recovery_matrix.py`.

Source and verifier provenance are bound by
`eval_cases/public_benchmark_v1/suite.yaml`.
