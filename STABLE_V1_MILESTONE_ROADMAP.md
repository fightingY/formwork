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

### 2.4 当前稳定线交接状态（2026-07-20）

- `archive/long-run-11-of-60` branch 和 `archive-long-run-11-of-60` tag 已存在，旧 5x12 cognition
  结果不再参与 Stable 主线开发或统计。
- `stable-v1.0` 至 `stable-v2.0.1` tag 已存在；当前正式能力基线为 Stable V2.0.1 acceptance。
- 本地旧 SWE、5x12 run、旧式 memory、开发报告副本和未被正式验收引用的 run 已清理；Git 中的
  archive ref 与 `acceptance/` 正式证据未删除。
- `.minicc/runs` 当前只保留 48 个正式 Stable eval run 和 1 个 V2.0 真实模型 checkpoint/resume run；
  版本索引已重建且 dangling pointer 为 0。
- 当前代码回归为 `132 passed`；V2.0.1 的 C02 release gate 为 3/3 PASS。
- Stable V2.0.1 已完成 workspace 证据一致性；下一开发阶段固定为 V2.0.2，账本验收后才能开始
  context compaction A/B。
- C05-C08、SWE-bench v2、working memory、runtime tools 和 meta review 均不属于 V2.0.1/V2.0.2，
  不得借技术债治理之名提前混入。

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
9. 正式 run 必须登记版本、阶段、case/轮次和原始 run 路径，并能从版本索引与 Viewer 定位。

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

### V2.0.1：Workspace Snapshot 证据一致性

目标：修复当前 `copytree + git init` 造成的 workspace 可见文件、Git 初始快照和最终 diff
范围不一致问题，保证 Agent 能看到和修改的项目文件都处于可解释、可审计的 workspace 边界内。

进入条件：`stable-v2.0` 已验收。旧 SWE-bench、`long_run_cognition_v1`、旧式 memory 和非正式
开发 run 已完成归档或本地清理，不再参与 Stable 主线统计。

本阶段只允许修改：

- workspace 的创建、复制和初始快照逻辑；
- ignored/untracked 文件进入 workspace 的显式规则；
- workspace manifest、diff 生成和相关测试；
- 为保持 V1.3/V2.0 行为所必需的最小 CLI 接线。

本阶段禁止同时引入：

- semantic compaction、working memory、runtime tools 或 meta review；
- 新 action/tool 类型；
- 新 benchmark case 或 SWE-bench 实验；
- run catalog、Viewer 信息架构或报告 schema 的大规模重构，这些属于 V2.0.2。

实现顺序：

1. 先增加回归测试，稳定复现“原仓库 tracked 文件因匹配 `.gitignore`，在新 workspace 中变成
   ignored/untracked，修改后不进入 diff”的问题。
2. Git 仓库优先从固定 `HEAD`/commit 创建独立 worktree 或等价的 Git 原生快照，不再通过重新
   `git init` 猜测原仓库的 tracked 状态。
3. 源工作区存在未提交修改时，显式记录 dirty 状态和 patch hash，并以可测试方式应用到 run
   workspace；不得静默丢失，也不得把 `.minicc/` 递归带入。
4. Git ignored 文件默认不复制。确有运行需要的 ignored 文件必须通过显式 allowlist 声明；敏感
   文件仍受硬性 deny 规则保护。
5. 非 Git 项目保留受控复制 fallback，并使用统一 ignore matcher，不再继续扩大散落在代码中的
   硬编码目录名单。
6. 每个新 run 生成 `workspace_manifest.json`，至少记录 source root、source commit、dirty 状态、
   dirty patch hash、snapshot mode、included/excluded 路径摘要和 ignore/allow 规则来源。

验收标准：

- 原仓库 tracked 文件即使匹配当前 `.gitignore`，进入 workspace 后仍保持 tracked，并且修改可被
  `diff.patch` 捕获。
- `.workbuddy/`、`.minicc/`、`.env`、虚拟环境、缓存和构建产物等 ignored/untracked 内容默认不进入
  普通 run workspace；显式 allowlist 的文件除外。
- 源工作区 tracked dirty change 能进入 workspace，且 manifest 中有确定性证据；未声明的 ignored
  文件不能因目录复制而混入。
- workspace 中所有允许 Agent 修改的项目文件，要么属于初始 Git snapshot，要么作为明确的
  untracked candidate 出现在最终 diff；不存在“可修改但不可审计”的文件。
- fixture eval 仍只从 case 的 `fixture/` 构建，不读取项目根目录的 `docs/`、`.workbuddy/`、历史 run
  或 acceptance 文件。
- `uv run pytest -q`、`git diff --check`、C02 连续 3 次和 V2.0 checkpoint 确定性回归全部通过。
- 每个验收 run 都生成 workspace manifest、state、trace、metrics 和 diff，且路径之间可相互定位。

验收归档（2026-07-20）：实现提交 `15713620f67c86dc31b73ac38d0ca969279552e8` 在 clean
worktree、固定 Docker digest 和 `release_gate=True` 下完成 C02 三连，结果为 3/3 PASS；完整回归
132/132 PASS，3 个版本索引条目均可定位且 dangling pointer 为 0。归档位于
`acceptance/stable-v2.0.1/`，稳定标签为 `stable-v2.0.1`。

失败回退：回到 `stable-v2.0`。不得用新增更多 ignore 名称掩盖 Git tracked 语义错误；不得在
workspace 证据仍不一致时进入 V2.0.2 或 V2.1。通过后标记 `stable-v2.0.1`。

### V2.0.2：Run / Suite / Report 技术账本

目标：把单次运行、一次评测套件、版本索引和人类可读报告拆成清晰实体，使新产生的证据不可变、
可定位、可迁移，并停止把正式 run、开发预检、报告副本和批次容器混放在 `.minicc/runs` 顶层。

进入条件：`stable-v2.0.1` 已验收，workspace manifest 与 diff 已可信。

本阶段只治理 V2.0.2 以后产生的新记录。历史 Stable acceptance 证据保持只读；已归档或已清理的
旧 SWE/5x12 实验不迁回主线，也不为追求历史字段齐全而伪造数据。

目标结构：

```text
.minicc/
  runs/<run-id>/
  suites/<suite-id>/
    manifest.json
    report.json
    report.md
  versions/<milestone>/
  artifacts/<content-hash-or-run-id>/
```

其中：

- `run` 表示一次 Agent 执行，目录创建后只追加运行证据，不被后续同名评测覆盖；
- `suite` 表示固定配置下一组 case/attempt，拥有唯一 `suite_id` 和不可变汇总；
- `version` 只保存指向真实 run/suite/acceptance 的轻量索引，不复制原始证据；
- `report` 是 suite 的派生产物，不再写入固定的 `eval_reports/eval_report.json` 覆盖上一轮结果；
- `artifact` 与 run 绑定，缺失时 Viewer 必须降级显示而不是崩溃。

统一新记录 schema，至少包含：

```text
schema_version
run_id
suite_id
milestone
stage
case_name / attempt
source_commit / workspace_manifest
provider / model / temperature
sandbox mode / image digest
status / result
task_success / agent_success / infrastructure_success / policy_outcome
started_at / completed_at
state / trace / metrics / diff / report paths
```

实现顺序：

1. 为 state、metrics、eval result、suite manifest 和 version entry 增加明确 `schema_version`，并为
   当前正式 Stable 证据提供只读兼容解析，不原地重写历史原始文件。
2. 引入唯一 `suite_id`；同一 suite 的所有 case run 必须记录相同配置快照和 suite 归属。
3. 报告按 suite 写入独立目录，连续运行两次不得覆盖第一次的 JSON、Markdown、CSV 或 run 指针。
4. 明确定义终态：`completed`、`failed`、`waiting_approval`、`interrupted`、`orphaned`。启动索引或
   Viewer 时识别长期残留的 `running`，但不得擅自把它计为任务失败或正式样本。
5. 版本索引只登记存在且 schema 可解析的 run/suite；缺 state、trace、metrics、diff 或 verifier
   结果的记录不得进入正式通过率分母。
6. Viewer 默认展示当前 milestone 的正式记录，并提供 development/history 过滤，不再把目录名猜测
   当作 run 类型。
7. 增加 retention/dry-run 清理入口：默认只列出可清理项，正式 acceptance 和被版本索引引用的 run
   永远不自动删除。

验收标准：

- 连续执行两个 suite 后，两套 manifest 和报告均保留，run id、suite id、version entry 可双向定位。
- 强制中断一次运行后，恢复扫描能将其识别为 `interrupted` 或 `orphaned`，不再永久显示 `running`。
- 正式报告的 task、agent、infrastructure、policy 四类结果语义独立且与原始 state/verifier 一致。
- 新 schema 报告不依赖“字段不存在等于 false”；旧 schema 显示 `legacy/unknown`，不参与不兼容指标。
- catalog 中 dangling run/suite 指针为 0；Viewer 面对缺少可选 artifact 的记录不崩溃。
- cleanup 命令 dry-run 与真实清理使用同一选择结果；默认保护所有正式 acceptance 引用。
- `uv run pytest -q`、`git diff --check`、V1.3 的 15-run 回归和 V2.0 checkpoint 回归不下降。
- 使用新账本完成一次 C02 `repeat=3`，结果必须形成 1 个 suite、3 个 run 和 1 组不可变报告。

失败回退：回到 `stable-v2.0.1`。不得通过手工复制报告或手改 manifest 让验收通过；账本未能做到
零覆盖、零 dangling pointer 时不得进入 V2.1。通过后标记 `stable-v2.0.2`。

### V2.1：上下文压缩 A/B

目标：单独证明 context compaction 的收益，不同时引入 working memory。

进入条件：`stable-v2.0.2` 已验收，workspace 与 run/suite/report 证据链均可信。

实验配置只保留 A0 无语义压缩、A1 语义压缩。先运行 1 个 case 各 3 次；稳定后扩展到至少 3 个 case。

实验开始前必须先校准缓存统计：run 级命中率按累计 hit/miss token 加权计算，供应商未返回缓存字段时
显示 `unsupported`，不得与真实 `0%` 命中混为一谈。该阶段只修指标，不宣称缓存优化收益。

验收标准：

- 所有实验均真实触发预期压缩事件，不接受“配置开启但未触发”。
- A1 的任务通过率不得低于 A0。
- A1 的平均 prompt 长度相对 A0 有稳定下降，并报告均值、最大值和样本数。
- 关键文件、根因、patch 状态和 artifact pointer 保留率为 100%。
- 重复文件读取和重复搜索不得显著高于 A0。
- 连续两轮独立复跑得到同方向结论。

失败回退：回到 `stable-v2.0`，压缩继续保留为 experimental，不进入简历主叙事。通过后标记 `stable-v2.1`。

### V2.1.1：Prompt Cache 命中优化 A/B

目标：在已经校准的统计口径上，单独验证稳定前缀布局能否提高供应商 Prompt Cache 的实际复用，
不同时改变 compaction、working memory、模型或任务断言。

进入条件：`stable-v2.1` 已验收。若供应商不返回缓存字段，则记录为 `unsupported`，该实验保留为
experimental，但不阻塞 V2.2。

实验配置只保留 P0 当前消息布局、P1 缓存优化布局。先对一个固定 prompt 序列各运行至少 5 次，
再在一个真实 case 上各运行 3 次；每轮固定 provider、model、temperature、system prefix 和动态输入顺序。

验收标准：

- P0/P1 均报告请求数、hit tokens、miss tokens、加权命中率、prompt tokens、延迟和任务结果。
- `unsupported`、真实 `0%` 和非零命中三种状态能够明确区分。
- P1 的任务通过率不得低于 P0，且稳定前缀的可复用 token 数不得下降。
- 只有连续两轮独立复跑均显示 P1 命中率或缓存 token 数提高，才允许声明缓存优化有效。
- 不设绝对命中率目标，不用增加重复请求、扩大 token 或降低验证强度制造虚假提升。

失败回退：保持 V2.1 消息布局，Prompt Cache 优化继续标记为 experimental。成功后标记
`stable-v2.1.1`，再进入 V2.2；失败不阻塞 V2.2。

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

进入条件：V1.2、V2.0、V2.0.1、V2.0.2 已通过；V2.1、V2.2 可选择性通过，未通过的能力必须
明确标为 experimental。

验收标准：

- README 首屏提供一条 10 分钟内可完成的演示路径。
- 固定 benchmark 能一条命令运行并生成 JSON、Markdown 和 CSV。
- 报告至少覆盖系统回归、上下文治理、记忆收益、断点恢复四个维度；未启用维度显示 empty/experimental，不伪造结果。
- 每个简历数字都有 case、run id、配置、原始 artifact 和复跑命令。
- 必须交付 `docs/ETCLOVG_CAPABILITY_MATRIX.md`，逐层记录能力声明、代码入口、测试、验收命令、
  run id、原始证据、当前状态和已知边界。
- ETCLOVG 矩阵中的状态只允许使用 `stable`、`experimental`、`not implemented`；只有已经通过
  版本验收且能从 Viewer 或验收归档定位原始 run 的能力才允许标记为 `stable`。
- V2.1/V2.2 未通过时不阻塞 V3.0，但 Context 层必须如实标记为 `experimental`，并保留对应的
  后续验收版本和复跑入口，不能用已有基础实现代替收益证据。
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
- workspace 可见文件无法被 manifest/diff 解释，或 version catalog 出现 dangling pointer。
- 同一次 suite 的报告覆盖上一轮结果，或新旧 schema 被混入同一通过率口径。

停止后只做：归因、最小复现、回归测试、单点修复。不得继续跑更大的矩阵。

## 6. 版本依赖

```text
archive/long-run-11-of-60 (5d7f163，仅归档)
                    |
8f19cd3 -> V1.0 -> V1.1 -> V1.2 -> V1.3 -> V2.0
                                      |        |
                                      |        +-> V2.0.1 workspace snapshot
                                      |                  |
                                      |                  +-> V2.0.2 run/suite/report ledger
                                      |                             |
                                      |                             +-> V3.0
                                      |                             |
                                      |                             +-> V2.1 compaction
                                      |                                    |
                                      |                                    +-> V2.1.1 prompt cache
                                      |                                               |
                                      |                                               +-> V2.2 memory
                                      |
                                      +-> experimental/runtime-tools
                                      +-> experimental/meta-review
```

V2.1 和 V2.2 是增强线，不应阻塞 V3.0 的 Harness 发布。这样即使实验能力没有产生收益，稳定项目仍然可以完成并用于面试。V3.0 仍必须通过 ETCLOVG 能力证据矩阵完整披露七层状态；“不阻塞发布”只表示允许标记为 `experimental`，不表示可以省略或宣称已经稳定。

## 7. 最终可声明的项目效果

Stable V3.0 最终不是追求和他人简历逐字一致，而是形成同等强度的证据链：

```text
实现 Coding Agent Harness 的模型接入、action/tool 治理、workspace 隔离、
PolicyChain、trace、metrics、diff 和 report 执行闭环；在固定回归任务上达到
可复现的通过率和预算内完成率；通过受控中断场景验证 checkpoint/resume 的
状态保真，并用 A/B 实验量化上下文压缩和分层记忆对 prompt 与重复 I/O 的影响。
```

具体百分比和优化数字必须由最终报告生成，不能提前写入项目说明或简历。
