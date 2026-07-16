# Stable V2.0 验收说明

## 验收目标

Stable V2.0 只验证 checkpoint/resume 状态保真，不新增 action 或工具能力。

核心要求：

- checkpoint 保存 workspace、run status、trajectory、metrics 和执行日志。
- 修改前、修改后验证前、验证失败后中断均可恢复完成。
- 10 个确定性状态场景全部创建 checkpoint 并完成 resume。
- workspace 漂移、错误 run、失效指针和疑似已执行 action 必须 100% 拒绝。
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

## 正式验收结果

验收日期：2026-07-16

验收代码：`8b6d3216400af367818d9983d51abe272d6b4f82`

| 范围 | 结果 |
| --- | ---: |
| 全量单元与集成测试 | 118/118 PASS |
| Checkpoint 专项测试 | 19/19 PASS |
| 确定性 resume 状态矩阵 | 10/10 PASS |
| workspace/status/trajectory/diff 一致性 | 10/10 PASS |
| 三个执行式中断场景 | 3/3 PASS |
| 漂移与歧义执行拒绝 | 3/3 PASS |
| 真实模型 checkpoint/resume | 1/1 PASS |
| V1.3 完整回归 | 15/15 PASS |

## 真实模型恢复证据

正式 run：`20260716-220053-493581e9`

执行过程：

1. 模型创建 `V2_CHECKPOINT_ACCEPTANCE.md`。
2. 第 1 个 trajectory step 后进入 `interrupted`。
3. `checkpoint-0004` 保存 workspace 指纹、trajectory 和完成态执行日志。
4. `resume --from-checkpoint` 校验并恢复同一 run。
5. 恢复后只执行 `cat V2_CHECKPOINT_ACCEPTANCE.md` 验证，没有重复创建文件。
6. 最终状态为 `completed`，`resume_count=1`，diff 与 workspace 内容一致。

关键指标：

- checkpoints_created：7
- turns：3
- bash actions：2
- 文件创建执行次数：1
- 重复执行次数：0
- provider errors：0
- infrastructure errors：0
- Docker 残留容器：0

原始证据保存在 `real-model-run/`。

## V1.3 回归结果

V2.0 自动 checkpoint 开启后，C01/C02/C03/C04/C09 各 3 次完整回归仍为 15/15 PASS；
任务结果、Agent 终态和基础设施状态均为 15/15 PASS。详细报告见 `v1.3-regression/`。

## 发布结论

Stable V2.0 已达到路线图要求：10 个恢复状态场景全部完成，三个基础中断场景全部通过，
错误 run、workspace 漂移和歧义执行均 fail-closed，真实模型在 resume 后通过最终验证，且 V1.3
固定矩阵无回退。可以归档验收提交并创建 `stable-v2.0` tag。
