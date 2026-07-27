# Stable V2.1 正式验收

## 结论

Stable V2.1 的 Context Compaction A/B 正式验收通过：两轮独立对比均为 `PASS`，共 `24/24`
个正式 run 通过。

- 发布版本：`2.1.0`
- 最终验收实现提交：`caebc1c3fe8b6af15c0d2ae5454ffb6a951caa98`
- 完整代码回归：`160/160 PASS`
- Provider：`https://api.siliconflow.cn/v1`
- Model：`deepseek-ai/DeepSeek-V4-Flash`
- Temperature：`0.0`
- Sandbox：Docker locked mode
- Docker image：`python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0`

## 正式结果

| 轮次 | 范围 | A0 | A1 | Prompt mean A0 → A1 | 降幅 |
|---|---|---:|---:|---:|---:|
| 1 | C02，各 3 次 | 3/3 | 3/3 | 3317.8 → 3010.4 | 9.27% |
| 2 | C02/C03/C07，各 3 次 | 9/9 | 9/9 | 10599.9 → 5660.7 | 46.60% |

两轮均满足：

- A0 每个 run 均真实触发 context budget；
- A1 每个 run 均真实完成 semantic compaction；
- A1 任务与逐 case 通过率均不低于 A0；
- 关键文件、根因、patch 状态和 artifact pointer 保留率为 100%；
- 重复 I/O 均处于验收容差内：第一轮 `0.00 → 0.33`，门限 `1.00`；第二轮
  `0.11 → 1.11`，门限 `1.11`；
- 两轮 prompt mean 均下降，结论方向一致。

## 证据

第一轮基于提交 `b6502e8f51b45fb058e189977f5b6c8e1db6efa8`：

- A0：`suite-20260721-114710-5f07d43c`
- A1：`suite-20260721-114932-d19d9a3c`

第二轮基于最终验收实现提交 `caebc1c3fe8b6af15c0d2ae5454ffb6a951caa98`：

- A0 汇总：`suite-20260727-v21-round2-a0-release-caebc1c`
- A1 汇总：`suite-20260727-v21-round2-a1-release-caebc1c`

第二轮汇总从同一实现提交上的不可变正式 suite 中选取通过的对应 attempt。每个 case 仍保留原始
`suite_id` 和 evidence pointer，未修改原始 run 或报告；汇总只统一形成 C02/C03/C07 各 3 次的
最终比较口径。A0 来源 suite 为 `suite-20260722-211003-7982fda7`、
`suite-20260727-103358-0e9c96b7`；A1 来源 suite 为 `suite-20260722-220111-5d21122f`、
`suite-20260727-083552-ede2bef9`、`suite-20260727-103415-d9c4c5c3`。

机器判定见 [report.json](context-compaction-ab/report.json)，可读报告见
[report.md](context-compaction-ab/report.md)。原始 run 和 suite 证据仍由 `.minicc/runs/` 与
`.minicc/suites/` 保存，本归档不复制中间尝试、工作区、trace 或 checkpoint。

Semantic compaction 至此升格为 Stable V2.1 能力；日常运行仍默认使用 deterministic strategy，
semantic strategy 需显式启用。本里程碑不声明 Prompt Cache 优化收益。
