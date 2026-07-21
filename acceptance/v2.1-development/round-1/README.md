# V2.1 Context Compaction A/B Round 1

## 结论

首轮单 case 实验完成，A0 与 A1 均为 `3/3 PASS`，本轮全部 A/B 指标门禁通过。由于路线图要求
连续两轮独立复跑且最终扩展到至少 3 个 case，当前总状态必须保持 `INCONCLUSIVE (1/2 rounds)`，
不得标记为 Stable V2.1。

## 验收对象

| 项目 | 值 |
|---|---|
| 实现提交 | `b6502e8f51b45fb058e189977f5b6c8e1db6efa8` |
| Git worktree | clean |
| Case | `V21_C02_fix_failing_test` |
| Repeat | A0 3 次；A1 3 次 |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| Sandbox | Docker locked mode |
| Docker image | `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0` |
| Release gate | `True` |
| Milestone | `v2.1-development` |
| Case context override | `max_prompt_chars=1`、`recent_turns=1`、`summary_max_chars=3000` |

## Suite

| Variant | Strategy | Suite | 结果 |
|---|---|---|---:|
| A0 | 完整 trajectory，不压缩 | `suite-20260721-114710-5f07d43c` | 3/3 PASS |
| A1 | 语义压缩 | `suite-20260721-114932-d19d9a3c` | 3/3 PASS |

正式命令：

```powershell
uv run minicc eval eval_cases/compaction_suite_v1 `
  --case V21_C02_fix_failing_test `
  --repeat 3 `
  --context-variant a0 `
  --release-gate `
  --milestone v2.1-development `
  --output-dir <clean-external-output-dir>\a0

uv run minicc eval eval_cases/compaction_suite_v1 `
  --case V21_C02_fix_failing_test `
  --repeat 3 `
  --context-variant a1 `
  --release-gate `
  --milestone v2.1-development `
  --output-dir <clean-external-output-dir>\a1
```

## A/B 指标

| 指标 | A0 | A1 | 判定 |
|---|---:|---:|---|
| 任务通过率 | 100% | 100% | PASS：A1 不下降 |
| Prompt chars mean | 3317.8 | 3010.4 | PASS：A1 降低 9.27% |
| Prompt chars max | 4547 | 3907 | 记录项 |
| Prompt samples | 16 | 20 | 记录项 |
| 关键事实保留率 | 100% | 100% | PASS |
| 平均重复读取/搜索 | 0.00 | 0.33 | PASS：未显著升高 |
| Cache 状态 / 加权命中率 | supported / 0% | supported / 0% | 已区分真实 0% 与 unsupported |
| 预期事件 | 3/3 跨过 budget | 3/3 成功语义压缩、零 fallback | PASS |

A1 额外压缩成本：7,289 prompt tokens、14,808 completion tokens、318,370 ms latency。该成本不混入
主 agent prompt/cache 口径。首轮证明 prompt 变短，但额外模型成本较高；扩大实验时必须继续披露，
不能只报告 9.27% 的 prompt 降幅。

## 归档内容

- `a0/suite-20260721-114710-5f07d43c/`：A0 immutable manifest 与 JSON/Markdown/CSV 报告；
- `a1/suite-20260721-114932-d19d9a3c/`：A1 immutable manifest 与 JSON/Markdown/CSV 报告；
- `comparison/`：首轮机器可读和 Markdown A/B 判定报告；
- canonical run 证据仍位于 `.minicc/runs/<run-id>/`，suite 账本位于 `.minicc/suites/<suite-id>/`。

## 下一门禁

下一轮应扩展到至少 3 个 case，对 A0/A1 各运行 3 次，并使用四个不同 suite id 生成两轮综合报告。
只有两轮方向一致且所有门禁继续通过，才能进入 Stable V2.1 归档与标签阶段。
