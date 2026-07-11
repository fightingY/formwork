# Stable V1.1 验收记录

验收日期：2026-07-11

基线 commit：`2ffc8d1`

发布版本：`minicc 1.1.0`

验收 tag：`stable-v1.1`

## 固定配置

| 项目 | 值 |
| --- | --- |
| Case | `C02_fix_failing_test` |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| Sandbox | Docker locked，`python:3.11-slim`，network none |
| Case budget | max_turns=10，max_bash_actions=25 |
| 执行方式 | Docker，不使用 `--execute-local` |

执行命令：

```powershell
uv run minicc eval eval_cases/capability_suite_v1/C02_fix_failing_test --repeat 3 --output-dir acceptance/stable-v1.1
```

原始汇总结果见同目录的 `eval_report.json` 和 `eval_report.md`。每个 `run_id` 指向 `.minicc/runs/<run_id>/` 下独立保留的 state、trace、metrics、diff、run report 和 verifier report。

## 验收结果

| Attempt | Run id | Status | Verifier | Turns | Bash actions | Duration | Approval | Budget exhaustion |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `eval-C02_fix_failing_test-r1-20260711-220338-4c654ec6` | completed | 3/3 PASS | 6 | 5 | 134144 ms | 0 | 0 |
| 2 | `eval-C02_fix_failing_test-r2-20260711-220555-d4172ba4` | completed | 3/3 PASS | 8 | 7 | 204834 ms | 0 | 0 |
| 3 | `eval-C02_fix_failing_test-r3-20260711-220922-2a5874a4` | completed | 3/3 PASS | 6 | 5 | 120413 ms | 0 | 0 |

汇总：3/3 run status 为 `completed`，3/3 Verifier 通过，3/3 仅修改允许目录 `src/`，3/3 最终测试命令 `python -m unittest discover -s tests` 通过。Trace 中 0 次 `approval_requested`，项目中未实现 provider fallback，且所有 Docker 容器在 run 后已清理。

三个 attempt 的最终 diff 完全一致，只将 `src/calculator.py` 的 `return a - b` 修正为 `return a + b`。

## 已恢复的命令失败

Attempt 1 曾执行不可用的 `pytest`，返回 exit code 127；attempt 2 曾执行 `python -m pytest`，返回模块不存在。两次模型均在同一预算内改用 fixture 支持的 `unittest` 命令并通过最终 Verifier。这些是已记录的 command failure，不属于审批、provider fallback、budget exhaustion 或最终验证失败。

## 发布结论

V1.1 的单任务执行闭环已通过。V1.2 应从 `stable-v1.1` 创建新分支，且只增加 C01、C03 或 C04 中的一个固定回归 case，不能修改已验收的 C02 基线。
