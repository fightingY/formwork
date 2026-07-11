# miniCC Stable V1 Milestone Roadmap

## 1. 目标

本路线只服务于一个目标：把 miniCC 收敛成一个可控、可观测、可恢复、可回归的 Coding Agent Harness，并为简历中的每一项结论提供可重复的实验依据。

稳定主线不再以“功能数量”作为完成标准。每个版本必须先通过自己的验收门，才能进入下一个版本。Semantic compaction、working memory、runtime tools 和 meta review 在被单独验证前，统一视为实验能力。

## 2. 基线与回退策略

### 2.1 保留当前版本

当前 `master@5d7f163` 不删除、不重写历史，作为 long-run cognition 失败实验的完整档案。开始稳定线前应创建：

```powershell
git branch archive/long-run-11-of-60 5d7f163
git tag archive-long-run-11-of-60 5d7f163
```

该版本的代码、60 个 run 和报告只用于复盘，不作为 Stable V1 的继续开发基线。

### 2.2 稳定线起点

Stable V1 从 `8f19cd3` 创建新分支：

```powershell
git switch -c stable-v1 8f19cd3
```

选择 `8f19cd3` 的原因：

- 已包含 M1-M6：Agent Loop、Workspace、Sandbox、Policy、Context、Trace、Metrics、Eval。
- 已包含只读 Web Trace Viewer 和 HITL case。
- 尚未进入后续 memory、SWE-bench、复杂 action、runtime tools、meta 和 12x5 benchmark 的连续扩张。
- 比现有 `stable-v1-baseline@1b1e8dc` 更完整，不需要重新建设 Trace 和 Eval。

### 2.3 每阶段的 Git 规则

- 每个里程碑从上一个已验收 tag 创建分支。
- 一个提交只表达一个行为变化。
- 每个里程碑验收通过后创建 annotated tag。
- 未通过时只允许在当前里程碑内修复，不得提前开发下一阶段。
- 需要放弃当前阶段时，回到上一个验收 tag 新建修复分支，不对共享分支执行 `reset --hard` 或强推。

推荐命名：

```text
分支: milestone/v1.1-single-case
标签: stable-v1.1
提交: fix(eval): prevent approval in locked benchmark case
```

## 3. 通用验收门

所有版本都必须满足：

1. `uv run pytest -q` 全部通过。
2. `git diff --check` 通过。
3. 工作区没有非预期文件和未解释 diff。
4. 每次真实运行都有 `state`、`trace`、`metrics`、`diff` 和最终报告。
5. 报告中的 `passed`、run status 和 Verifier 结果语义一致。
6. 非 HITL 专用实验不得进入 `waiting_approval`。
7. 失败必须归因，禁止用提高预算掩盖 harness、policy 或 case 问题。
8. 当前版本文档只声明已经通过验收的能力。

任意一项不满足，版本不得打 tag，也不得进入下一阶段。

## 4. 版本路线

### V1.0：基线恢复

目标：从 `8f19cd3` 恢复一个可开发、可测试的稳定基线，不新增能力。

工作范围：

- 验证 Python 环境、Docker、CLI 和测试套件。
- 核对 M1-M6 与 Web Viewer 的真实可用状态。
- 把未验证能力从 Stable V1 的 README 叙事中排除。
- 建立统一的验收结果目录和版本记录。

进入条件：

- 当前 `5d7f163` 已创建 archive branch 和 tag。
- `stable-v1` 已从 `8f19cd3` 创建。

验收标准：

- 通用验收门全部通过。
- `minicc --help`、`minicc run --help`、`minicc eval --help`、`minicc web --help` 正常。
- 一个不调用真实模型的 fake-provider loop 测试能够完整结束。
- Stable V1 文档中不存在未经验证的成功率或能力数字。

失败回退：回到 `8f19cd3`，只修环境或基线兼容问题。通过后标记 `stable-v1.0`。

### V1.1：单任务执行闭环

目标：让 `C02_fix_failing_test` 成为第一个绝对可靠的真实模型演示。

进入条件：`stable-v1.0` 已验收。

验收标准：

- 相同 provider、模型、温度和预算下连续运行 3 次。
- 3/3 测试通过，3/3 Verifier 通过。
- 3/3 状态为 `completed`。
- 0 次 `waiting_approval`，0 次 budget exhaustion，0 次 provider fallback。
- 3/3 只修改 case 允许的文件。
- 每次都生成可定位的 trace、metrics、diff 和报告。

失败处理：首次失败只做归因；同类失败复现后增加最小回归测试；修复后重新从第 1 次开始计数。通过后标记 `stable-v1.1`。

### V1.2：四个固定回归任务

目标：建立简历中“固定回归任务”的可信基础。

任务范围：

- C01 仓库理解。
- C02 修复失败测试。
- C03 添加 CLI 小功能。
- C04 添加回归测试。

进入条件：`stable-v1.1` 已验收。

验收标准：

- 每个 case 连续运行 3 次，总计 12 次。
- 12/12 Verifier 通过，12/12 在预算内完成。
- 0 次非预期审批，0 次 workspace 污染。
- 每个 case 的断言只验证一个主要能力，失败归因没有 `unknown`。
- 汇总报告能按 case 展示通过率、turns、tool actions、耗时和 diff 范围。

注意：只有真实达到时，简历才允许写“4 个固定回归任务通过率、预算内完成率、Verifier 通过率均为 100%”。

失败回退：回到 `stable-v1.1`。一次只引入一个 case；新增 case 未通过不得影响已验收 case。通过后标记 `stable-v1.2`。

### V1.3：工具治理与安全边界

目标：证明 Agent 的 action、工具执行、权限和审计闭环稳定，不追求工具数量。

进入条件：`stable-v1.2` 已验收。

验收标准：

- 每一种对外声明的 action/tool 都有参数校验、trace 事件和错误 observation 测试。
- locked 模式的普通评测不会触发审批。
- HITL 专用 case 能稳定进入审批、批准后恢复、拒绝后终止。
- 危险命令、越界路径和联网动作各有 allow/deny/approval 回归测试。
- 工作区内修改与 run-local artifact/tool 不互相污染。
- C01-C04 的 12 次回归结果不低于 V1.2。

失败回退：回到 `stable-v1.2`。不要为了模仿他人简历强行凑“7 类工具”；只统计真实存在且经过验证的工具。通过后标记 `stable-v1.3`。

### V2.0：Checkpoint / Resume 状态保真

目标：把恢复机制做成 Stable V1 的第二个核心卖点。

进入条件：`stable-v1.3` 已验收。

第一轮只做 3 个确定性场景：修改前中断、修改后验证前中断、验证失败后中断。三者通过后再扩展到 10 个场景。

验收标准：

- 10/10 checkpoint 创建成功，10/10 resume 完成。
- 10/10 workspace、run status、trajectory 和 diff 校验一致。
- 中断前已完成的修改或高风险 action 重复执行次数为 0。
- 旧 run、错误 workspace 或失效 checkpoint 的漂移识别率为 100%。
- 不存在误信旧状态继续执行的情况。
- 至少一个真实模型 case 在 resume 后通过最终 Verifier。

失败回退：3 个基础场景未全过时，不得扩到 10 个；回到 `stable-v1.3`。通过后标记 `stable-v2.0`。

### V2.1：上下文压缩 A/B

目标：单独证明 context compaction 的收益，不同时引入 working memory。

进入条件：`stable-v2.0` 已验收。

实验配置只保留 A0 无语义压缩、A1 语义压缩。先运行 1 个 case 各 3 次；稳定后扩展到至少 3 个 case。

验收标准：

- 所有实验均真实触发预期压缩事件，不接受“配置开启但未触发”。
- A1 的任务通过率不得低于 A0。
- A1 的平均 prompt 长度相对 A0 有稳定下降，并报告均值、最大值和样本数。
- 关键文件、根因、patch 状态和 artifact pointer 保留率为 100%。
- 重复文件读取和重复搜索不得显著高于 A0。
- 连续两轮独立复跑得到同方向结论。

失败回退：回到 `stable-v2.0`，压缩继续保留为 experimental，不进入简历主叙事。通过后标记 `stable-v2.1`。

### V2.2：分层记忆与 Follow-up

目标：证明记忆能够减少重复 I/O，而不是只证明文件被写入。

进入条件：`stable-v2.1` 已验收。

先做 1 个两阶段 follow-up task，各配置运行 3 次；成功后扩到 3 个，最后才允许扩到 12 个 memory dependency task。

验收标准：

- short-term、long-term、working memory 的所有权和生命周期有明确测试。
- Follow-up 阶段关键事实回答正确率为 100%。
- 开启记忆后重复文件读取次数相对无记忆基线稳定下降。
- 旧 run 记忆串入当前 run 的次数为 0。
- 无关记忆注入率和错误记忆采纳率均为 0。
- 报告给出原始命令、trace 证据、读取次数和 prompt 成本。
- 只有实际测得时，才允许写“12 个任务从 N 次降到 M 次”，不得预设 `60 -> 0`。

失败回退：回到 `stable-v2.1`，working memory 降级为 experimental。通过后标记 `stable-v2.2`。

### V3.0：评测闭环与简历发布版

目标：形成可演示、可复跑、可用于简历陈述的发布版本。

进入条件：V1.2、V2.0 已通过；V2.1、V2.2 可选择性通过，未通过的能力必须明确标为 experimental。

验收标准：

- README 首屏提供一条 10 分钟内可完成的演示路径。
- 固定 benchmark 能一条命令运行并生成 JSON、Markdown 和 CSV。
- 报告至少覆盖系统回归、上下文治理、记忆收益、断点恢复四个维度；未启用维度显示 empty/experimental，不伪造结果。
- 每个简历数字都有 case、run id、配置、原始 artifact 和复跑命令。
- 新机器按照 runbook 可以完成安装、单 case 运行和报告查看。
- Web Viewer 缺少可选 artifact 时不崩溃。

失败回退：回到最近已验收 tag，只发布已经被证明的能力。通过后标记 `stable-v3.0`。

### V3.x：可选研究版本

Runtime tool synthesis 和 meta review 不属于 V3.0 的必需项。每次只能选择其中一个方向，并使用独立的 `experimental/*` 分支。

进入 Stable 的最低条件：

- 先有确定性单元和集成测试。
- 再有 1 个真实 case 连续 3 次通过。
- 功能开启后的通过率不低于关闭时。
- 指标证明实际使用，而不是只有配置开关或 prompt 文案。
- 不改变 V1.2 固定回归和 V2.0 resume 的结果。

## 5. 停止规则

出现以下任一情况，立即停止扩大实验规模：

- 同一基础设施或 policy 失败连续出现 2 次。
- 出现 `waiting_approval`，但 case 不是 HITL 专用 case。
- 被测功能的核心指标始终为 0。
- run 没有结束状态，或报告状态与真实 state 不一致。
- 为了通过实验需要同时修改 harness、case、断言和报告生成器。
- 单次里程碑需要新增超过约 10 个生产文件或同时触及 3 个以上能力域。

停止后只做：归因、最小复现、回归测试、单点修复。不得继续跑更大的矩阵。

## 6. 版本依赖

```text
archive/long-run-11-of-60 (5d7f163，仅归档)
                    |
8f19cd3 -> V1.0 -> V1.1 -> V1.2 -> V1.3 -> V2.0 -> V3.0
                                      |        |
                                      |        +-> V2.1 compaction -> V2.2 memory
                                      |
                                      +-> experimental/runtime-tools
                                      +-> experimental/meta-review
```

V2.1 和 V2.2 是增强线，不应阻塞 V3.0 的 Harness 发布。这样即使实验能力没有产生收益，稳定项目仍然可以完成并用于面试。

## 7. 最终可声明的项目效果

Stable V3.0 最终不是追求和他人简历逐字一致，而是形成同等强度的证据链：

```text
实现 Coding Agent Harness 的模型接入、action/tool 治理、workspace 隔离、
PolicyChain、trace、metrics、diff 和 report 执行闭环；在固定回归任务上达到
可复现的通过率和预算内完成率；通过受控中断场景验证 checkpoint/resume 的
状态保真，并用 A/B 实验量化上下文压缩和分层记忆对 prompt 与重复 I/O 的影响。
```

具体百分比和优化数字必须由最终报告生成，不能提前写入项目说明或简历。
