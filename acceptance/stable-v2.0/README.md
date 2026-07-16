# Stable V2.0 验收说明

## 验收目标

Stable V2.0 只验证 checkpoint/resume 状态保真，不新增 action 或工具能力。

核心要求：

- checkpoint 保存 workspace、run status、trajectory、metrics 和执行日志。
- 修改前、修改后验证前、验证失败后中断均可恢复完成。
- 10 个确定性状态场景全部创建 checkpoint 并完成 resume。
- workspace 漂移、错误 run、损坏 checkpoint 和疑似已执行 action 必须 100% 拒绝。
- 中断前已完成 action 和高风险 action 自动重复执行次数为 0。
- 至少一个真实模型 run 在恢复后通过最终文件、diff 和终态校验。
- Stable V1.3 的 C01-C04/C09 完整矩阵不得回退。

## 固定环境

| 项目 | 值 |
| --- | --- |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| Sandbox | Docker locked，network none |
| Docker image | `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0` |

## 证据结构

- `checkpoint_report.json`：确定性场景、漂移拒绝和真实模型恢复的机器可读汇总。
- `real-model-run/`：真实模型 run 的 state、trace、diff、run report 和最新 checkpoint 指针。
- `v1.3-regression/`：V1.3 固定矩阵在 V2.0 代码上的完整回归报告。

## 当前状态

开发中。正式报告必须在干净的不可变 Git commit 上生成；开发期试跑不计入最终通过率。
