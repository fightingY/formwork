# miniCC ETCLOVG Capability Matrix

本文把 miniCC 按七个工程层披露。ETCLOVG 在本项目中定义为：Execution、Tools、Context、
Learning/Memory、Observability、Verification、Governance。状态只使用 `stable`、`experimental`、
`not implemented`；`stable` 必须已有版本 tag 和可定位的正式 run/归档。

| 层 | 能力声明 | 状态 | 代码入口 | 确定性测试 | 验收/复跑命令 | 正式 run/suite | 原始证据 | 已知边界 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E | AgentLoop 编排模型、action、observation 与持久化终态 | stable | `src/minicc/core/loop.py`, `runner.py`, `state.py` | `tests/test_loop.py`, `tests/test_session.py` | `uv run minicc eval eval_cases/capability_suite_v1 --case C02_fix_failing_test --repeat 3` | C02: `eval-C02_fix_failing_test-r1-20260716-211508-2b14bd47` 等 3 个 run | `acceptance/stable-v1.3/eval_report.json` | Bash-first 单 Agent，不是多 Agent 调度器 |
| E | Checkpoint 恢复保持 workspace/trajectory/diff，已完成 action 不重放 | stable | `src/minicc/core/checkpoint.py`, `src/minicc/cli.py:resume_command` | `tests/test_checkpoint.py` | `uv run minicc resume <run_id> --from-checkpoint` | `20260716-220053-493581e9` | `acceptance/stable-v2.0/real-model-run/` | 对 workspace 漂移和歧义执行 fail-closed，不做跨仓库迁移 |
| T | 严格 `bash/ask/final` action 协议与统一执行入口 | stable | `src/minicc/core/protocol.py`, `action_handler.py` | `tests/test_protocol.py`, `tests/test_action_handler.py` | `uv run minicc eval eval_cases/capability_suite_v1 --case C03_add_cli_option --repeat 3` | C03: `eval-C03_add_cli_option-r1-20260716-211535-f885d128` 等 3 个 run | `acceptance/stable-v1.3/eval_report.json` | 没有通用 typed tool registry；能力主要落到 shell |
| T | Runtime tool synthesis | not implemented | 无 | 无 | 无 | 无 | 无 | 不属于 V3.0，若研究必须进入独立 experimental 分支 |
| C | Stable Prefix / Dynamic Context、预算治理、semantic compaction | stable | `src/minicc/core/context.py`, `src/minicc/memory/compaction.py` | `tests/test_context.py`, `tests/test_compaction_ab.py` | `uv run minicc compaction-report --a0 <r1-a0> --a1 <r1-a1> --a0 <r2-a0> --a1 <r2-a1> --output-dir <output>` | `suite-20260721-114710-5f07d43c`, `suite-20260721-114932-d19d9a3c`, `suite-20260727-v21-round2-a0-release-caebc1c`, `suite-20260727-v21-round2-a1-release-caebc1c` | `acceptance/stable-v2.1/context-compaction-ab/report.json` | semantic strategy 需显式启用；Provider cache 命中不是本地可强制保证的能力 |
| C | Epoch prompt 布局提高长任务缓存利用率 | stable | `src/minicc/core/context.py`, `src/minicc/evals/cache_probe_runner.py` | `tests/test_context.py`, `tests/test_cache_utilization.py` | 见 `acceptance/stable-v2.1.2/report.md` 的两轮固定命令 | `formal-v212-round-81/82` 所列 C02/C07 suites | `acceptance/stable-v2.1.2/evidence.json` | 指标绑定当前 Provider/模型；换 Provider 必须重新测量 |
| L | 显式来源 working memory 减少 Follow-up 重复读取 | stable | `src/minicc/memory/working.py`, `src/minicc/evals/memory_ab.py` | `tests/test_memory.py`, `tests/test_memory_acceptance.py` | `uv run minicc memory-eval eval_cases/memory_suite_v1/M01_service_contract_follow_up --repeat 3 --execution-order alternating` | `suite-20260802-130812-5862115e`, `suite-20260802-131105-3763ea38`, `suite-20260802-131409-441c511f`（27 runs） | `acceptance/stable-v2.2/evidence.json` | 只接受显式 source run 和有限文件行区间，不做环境式自动检索 |
| L | Skill/Feedback Memory 自动选择与提取 | experimental | `src/minicc/skills/registry.py`, `src/minicc/memory/feedback.py` | `tests/test_skills.py`, `tests/test_memory.py` | `uv run pytest -q tests/test_skills.py tests/test_memory.py` | 无正式收益 run | 单元测试输出 | 有实现但没有独立版本收益验收，禁止写成稳定 RAG/长期记忆 |
| O | JSONL trace、metrics、diff 与只读 Web Viewer | stable | `src/minicc/trace/`, `src/minicc/server/app.py` | `tests/test_trace.py`, `tests/test_server.py` | `uv run minicc web --host 127.0.0.1 --port 8000` | V1.3 15 runs；V2.0 resume run | `.minicc/runs/<run_id>/`, `acceptance/stable-v2.0/real-model-run/` | 单机文件账本；不是分布式 observability backend |
| V | 声明式 eval、12 类断言、不可变 run/suite/version 账本 | stable | `src/minicc/evals/runner.py`, `assertions.py`, `src/minicc/core/ledger.py` | `tests/test_evals.py`, `tests/test_ledger.py`, `tests/test_run_catalog.py` | README 中固定 C01/C02/C03/C04/C09 命令 | V1.3 15 runs，V2.0.2 18 个 schema-v2 正式 runs | `acceptance/stable-v2.0.2/` | 指标资格依赖证据完整性；旧 schema 不与新口径静默混算 |
| V | 四维发布证据聚合（系统/Context/Memory/Resume） | experimental | `src/minicc/evals/release_report.py`, `minicc release-report` | `tests/test_release_report.py` | `uv run minicc release-report` | 开发报告：`.minicc/release-reports/v3-development-first/` | 同目录 JSON/Markdown/CSV/manifest | 尚未完成 V3.0 正式验收，因此当前只能标 experimental |
| G | PolicyChain、Docker locked sandbox、HITL 审批与敏感路径治理 | stable | `src/minicc/policy/`, `src/minicc/sandbox/`, `src/minicc/core/session.py` | `tests/test_policy.py`, `tests/test_docker_runner.py`, `tests/test_session.py` | `uv run minicc eval eval_cases/capability_suite_v1 --case C09_hitl_destructive_command --repeat 3` | C09: `eval-C09_hitl_destructive_command-r1-20260716-211708-e7b98bf0` 等 3 个 run | `acceptance/stable-v1.3/eval_report.json` | Docker 配置降低风险但不是完整恶意代码隔离证明；网络 dev 模式需显式授权 |

## 状态升级规则

- `experimental -> stable`：必须有独立版本验收、固定提交、完整 run/suite 和可复跑命令。
- `not implemented -> experimental`：先有确定性测试，再进入独立实验分支。
- Viewer 或 `.minicc` 中的原始 run 被清理前，必须先确认 acceptance 已保留足够的不可变证据入口。
- 本矩阵不得因 README 或简历措辞而反向提升状态；只能由验收结果驱动。
