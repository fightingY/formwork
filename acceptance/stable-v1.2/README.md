# Stable V1.2 验收记录

验收日期：2026-07-13

发布版本：`minicc 1.2.0`

验收 tag：`stable-v1.2`（待本次变更提交后创建）

## 验收范围

Stable V1.2 固定验收以下四个能力：

- `C01_repo_onboarding`：理解仓库并新增交接文档。
- `C02_fix_failing_test`：定位并修复失败测试。
- `C03_add_cli_option`：实现 CLI 小功能并补测试。
- `C04_add_regression_test`：只添加回归测试，不修改业务代码。

每个 case 连续运行 3 次，共 12 个独立 run。所有模型 action 均在 locked Docker sandbox 内执行。

## 固定配置

| 项目 | 值 |
| --- | --- |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| Provider transport | streaming，单次 timeout 120 秒，瞬时传输失败最多重试 2 次 |
| Sandbox | Docker locked，`python:3.11-slim`，network none |
| C01 budget | max_turns=8，max_bash_actions=20 |
| C02 budget | max_turns=10，max_bash_actions=25 |
| C03 budget | max_turns=12，max_bash_actions=30 |
| C04 budget | max_turns=10，max_bash_actions=20 |
| 执行方式 | Docker，不使用 `--execute-local` |

执行命令：

```powershell
uv run minicc eval eval_cases `
  --case C01_repo_onboarding `
  --case C02_fix_failing_test `
  --case C03_add_cli_option `
  --case C04_add_regression_test `
  --repeat 3 `
  --output-dir acceptance/stable-v1.2
```

原始汇总结果见同目录的 `eval_report.json` 和 `eval_report.md`。每个 `run_id` 指向 `.minicc/runs/<run_id>/` 下独立保留的 state、trace、metrics、diff、run report 和 verifier report。

## 汇总结果

| Case | 通过率 | 平均 turns | 平均 bash actions | 平均耗时 | Diff 范围 |
| --- | ---: | ---: | ---: | ---: | --- |
| C01 | 3/3（100%） | 8.00 | 7.00 | 86070 ms | `ONBOARDING.md` |
| C02 | 3/3（100%） | 6.67 | 5.67 | 94686 ms | `src/calculator.py` |
| C03 | 3/3（100%） | 7.00 | 6.00 | 114650 ms | `src/demo_cli.py`、`tests/test_cli.py` |
| C04 | 3/3（100%） | 6.00 | 5.00 | 65926 ms | `tests/test_parser.py` |

总计 12/12 run status 为 `completed`，12/12 Verifier 通过，12/12 在 case 预算内结束。Trace/metrics 中 provider errors 总数为 0，非预期审批总数为 0；所有 Docker 容器均在 run 后清理。

## Run 证据

| Case | Attempt | Run id | Verifier | Turns | Bash actions |
| --- | ---: | --- | --- | ---: | ---: |
| C01 | 1 | `eval-C01_repo_onboarding-r1-20260713-002945-583b8c58` | 3/3 PASS | 8 | 7 |
| C02 | 1 | `eval-C02_fix_failing_test-r1-20260713-003108-d4c3e829` | 3/3 PASS | 6 | 5 |
| C03 | 1 | `eval-C03_add_cli_option-r1-20260713-003245-720a73e4` | 4/4 PASS | 7 | 6 |
| C04 | 1 | `eval-C04_add_regression_test-r1-20260713-003544-bfcd58b2` | 3/3 PASS | 5 | 4 |
| C01 | 2 | `eval-C01_repo_onboarding-r2-20260713-003624-aa2475e9` | 3/3 PASS | 8 | 7 |
| C02 | 2 | `eval-C02_fix_failing_test-r2-20260713-003757-2f15ff5e` | 3/3 PASS | 8 | 7 |
| C03 | 2 | `eval-C03_add_cli_option-r2-20260713-004009-a97867f0` | 4/4 PASS | 7 | 6 |
| C04 | 2 | `eval-C04_add_regression_test-r2-20260713-004135-57c96e21` | 3/3 PASS | 8 | 7 |
| C01 | 3 | `eval-C01_repo_onboarding-r3-20260713-004243-7d762f83` | 3/3 PASS | 8 | 7 |
| C02 | 3 | `eval-C02_fix_failing_test-r3-20260713-004411-23c6c5e0` | 3/3 PASS | 6 | 5 |
| C03 | 3 | `eval-C03_add_cli_option-r3-20260713-004512-957c2323` | 4/4 PASS | 7 | 6 |
| C04 | 3 | `eval-C04_add_regression_test-r3-20260713-004639-84ddbe2b` | 3/3 PASS | 5 | 4 |

## 稳定性修复

V1.2 在正式验收前修复了 Windows/Docker UTF-8 输出、run 终态持久化、新增文件 diff、runtime cache 污染、provider error 收敛、常见 JSON action 外壳兼容、streaming 传输和有限重试。C04 的回合预算依据真实三连跑从 8 调整为 10，断言和允许修改范围未放宽。

## 发布结论

Stable V1.2 的四任务固定回归矩阵已通过。提交本次代码与归档后创建 annotated tag `stable-v1.2`；V1.3 从该 tag 开始验证工具治理与安全边界。
