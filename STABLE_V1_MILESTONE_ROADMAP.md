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

### 2.4 当前稳定线交接状态（2026-08-02）

- `archive/long-run-11-of-60` branch 和 `archive-long-run-11-of-60` tag 已存在，旧 5x12 cognition
  结果不再参与 Stable 主线开发或统计。
- `stable-v1.0` 至 `stable-v3.0` tag 已存在；当前正式能力基线为 Stable V3.0 acceptance。
- 本地旧 SWE、5x12 run、旧式 memory、开发报告副本和未被正式验收引用的 run 已清理；Git 中的
  archive ref 与 `acceptance/` 正式证据未删除。
- `.minicc/runs` 与 `.minicc/suites` 保留正式验收引用的原始 run/suite；失败和中断尝试不复制进
  acceptance 归档，也不混入最终通过口径。
- Stable V3.0 基线回归为 `296 passed`，Stable V3.1 Meta Review 为 `307 passed`；V2.2 的 M01/M02/M03 共 27 个正式 run 全部 PASS，M0/M1
  关键事实正确率均为 9/9，重复来源读取为 `9 -> 0`。
- Stable V2.1 已完成 context compaction 两轮独立 A/B；Stable V2.1.1 已完成 Prompt Cache
  两轮独立 A/B，semantic compaction 与追加式稳定前缀布局均升格为稳定能力。V2.1.1 只证明
  短任务上的相对改善，不代表已经达到高缓存利用率；绝对命中率与长任务前缀生命周期由
  V2.1.2 已把 epoch 布局与高缓存利用率升格为稳定能力。V2.2 已把显式来源 working memory
  与 Follow-up 配对链路升格为稳定能力；Skill/Feedback Memory 仍保持 experimental。
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

验收归档（2026-07-20）：最终实现提交 `3c1cd53b9fd46681edafcbb256e89241adb55003`
在 clean worktree、固定 Docker digest 和 `release_gate=True` 下完成 C02 三连与 V1.3 五案例三轮回归，
结果分别为 3/3 和 15/15 PASS；18/18 最终正式 run 的 evidence 与 metric eligibility 均有效，缺失
证据和重复 run id 均为 0。完整代码回归为 147/147 PASS，cleanup dry-run 候选为 0。归档位于
`acceptance/stable-v2.0.2/`，稳定标签为 `stable-v2.0.2`。发布门发现的 HITL 指标资格误判和
NetworkPolicy here-doc 正文误报均已通过最小回归测试修复，失败/预修复 suite 保留在归档 history。

失败回退：回到 `stable-v2.0.1`。不得通过手工复制报告或手改 manifest 让验收通过；账本未能做到
零覆盖、零 dangling pointer 时不得进入 V2.1。通过后标记 `stable-v2.0.2`。

### V2.1：上下文压缩 A/B

目标：单独证明 context compaction 的收益，不同时引入 working memory。

进入条件：`stable-v2.0.2` 已验收，workspace 与 run/suite/report 证据链均可信。

实验配置只保留 A0 无语义压缩、A1 语义压缩。先运行 1 个 case 各 3 次；稳定后扩展到至少 3 个 case。

开发入口（2026-07-21）：代码版本已进入 `2.1.0.dev0`。A0 明确定义为超过同一 context budget 后仍
保留完整 trajectory，A1 在相同阈值与 recent window 下执行结构化语义压缩；日常非实验运行继续
默认使用 V2.0.2 的确定性摘要。`eval_cases/compaction_suite_v1` 提供 3 个专项 case，
`minicc compaction-report` 强制要求两轮不同 suite id 才可能给出 PASS。此记录只表示开发入口就绪，
不表示 `stable-v2.1` 已验收。

实验开始前必须先校准缓存统计：run 级命中率按累计 hit/miss token 加权计算，供应商未返回缓存字段时
显示 `unsupported`，不得与真实 `0%` 命中混为一谈。该阶段只修指标，不宣称缓存优化收益。

验收标准：

- 所有实验均真实触发预期压缩事件，不接受“配置开启但未触发”。
- A1 的任务通过率不得低于 A0。
- A1 的平均 prompt 长度相对 A0 有稳定下降，并报告均值、最大值和样本数。
- 关键文件、根因、patch 状态和 artifact pointer 保留率为 100%。
- 重复文件读取和重复搜索不得显著高于 A0。
- 连续两轮独立复跑得到同方向结论。

验收归档（2026-07-27）：两轮独立 A/B 均为 PASS。第一轮在提交
`b6502e8f51b45fb058e189977f5b6c8e1db6efa8` 上完成 C02 的 A0/A1 各 3 次；第二轮在最终验收实现提交
`caebc1c3fe8b6af15c0d2ae5454ffb6a951caa98` 上完成 C02/C03/C07 的 A0/A1 各 9 次。两轮 A0/A1
任务通过率均为 100%，A1 平均 prompt 长度分别下降 9.27% 和 46.60%，关键事实保留率均为 100%，
重复 I/O 均满足容差门限；完整代码回归为 160/160 PASS。归档位于
`acceptance/stable-v2.1/`，稳定标签为 `stable-v2.1`。

失败回退：回到 `stable-v2.0`，压缩继续保留为 experimental，不进入简历主叙事。通过后标记 `stable-v2.1`。

### V2.1.1：Prompt Cache 命中优化 A/B

目标：在已经校准的统计口径上，单独验证稳定前缀布局能否提高供应商 Prompt Cache 的实际复用，
不同时改变 compaction、working memory、模型或任务断言。

进入条件：`stable-v2.1` 已验收。若供应商不返回缓存字段，则记录为 `unsupported`，该实验保留为
experimental，但不阻塞 V2.2。

实验配置只保留 P0 当前消息布局、P1 缓存优化布局。先对一个固定 prompt 序列各运行至少 5 次，
再在一个真实 case 上各运行 3 次；每轮固定 provider、model、temperature、system prefix 和动态输入顺序。

开发入口（2026-07-27）：代码版本进入 `2.1.1.dev0`，默认保持 P0 `rebuild`，P1
`append` 仅通过实验变体显式启用。Stable V2.1 的 176 个正式主请求均有缓存字段，其中
hit/miss 为 `1,024 / 325,088`，加权命中率 `0.314%`；这是真实低命中基线，不是
`unsupported`。V2.1.1 使用专用固定序列探针和真实 eval suite 分开保存证据，最终报告必须同时
展示实际 token、请求级状态、延迟、任务结果及稳定前缀哈希，不用探针结果掩盖真实任务退化。
两轮分别使用独立的 prompt namespace 并倒置 P0/P1 顺序；固定探针锁定为 5 次（前 2 次
warm-up、后 3 次 steady-state），真实 case 的 P0/P1 均须 3/3 PASS。真实 case 关闭可变的
Feedback Memory，避免仓库外状态成为混杂变量。正式报告拒绝 provider 实际重试、
缺失缓存字段、prompt token 膨胀、重复 run/namespace、未校验 manifest/hash 或不完整证据，
combined 指标仅展示，不作为可被 workload 权重影响的发布门禁。

验收归档（2026-07-29）：最终实现提交
`b258f98c6b1c7cc33c80f09052ce944de146776e` 上完成 `round-19`（P1→P0）与
`round-20`（P0→P1）两轮独立正式 A/B。P0/P1 真实 C02 均为 3/3 PASS，所有正式请求均为
单次 Provider attempt。P1 的真实命中率由 3.32%/3.40% 提高到 23.20%/24.45%，未缓存 token
分别下降 31.75%/34.77%，总 prompt 分别下降 14.08%/16.60%。完整归档位于
`acceptance/stable-v2.1.1/`，默认布局升格为 `append`，稳定标签为 `stable-v2.1.1`。

后续审计（2026-07-29）：C02 的 P1 每个 run 只有 4–5 个请求，首个请求约 546 prompt
tokens；在这段短序列内，消息数为 `2 -> 4 -> 6 -> 8 -> 10`，没有触发 compaction 或
`recent_turns` 滑窗，应用消息确实保持严格追加。去掉每个 run 的首个冷请求后，两轮实际
命中率也只有 26.48%/28.11%，所以低命中不能只归因于冷启动。按“下一请求最多复用上一请求
输入”计算，应用侧未取整的全链路理论上限为 72.29%/70.56%；实际只兑现其中约三到四成。
正式证据中的非零 hit 以 256-token 倍数出现，但该粒度未见供应商公开契约，因此只能记录为
本次 Provider 的经验现象，不能硬编码为跨 Provider 规则。V2.1.1 的结论仍然有效，但只能
表述为“相对改善且减少 miss tokens”，不得外推为“已达到 70%–90% 的高利用率”。

验收标准：

- P0/P1 均报告请求数、hit tokens、miss tokens、加权命中率、prompt tokens、延迟和任务结果。
- `unsupported`、真实 `0%` 和非零命中三种状态能够明确区分。
- P1 的任务通过率不得低于 P0，且稳定前缀的可复用 token 数不得下降。
- 只有连续两轮独立复跑均显示 P1 命中率或缓存 token 数提高，才允许声明缓存优化有效。
- 不设绝对命中率目标，不用增加重复请求、扩大 token 或降低验证强度制造虚假提升。

失败回退：保持 V2.1 消息布局，Prompt Cache 优化继续标记为 experimental。成功后标记
`stable-v2.1.1`。根据后续正式证据审计，下一阶段调整为 V2.1.2，不再从 V2.1.1 直接进入
V2.2。

### V2.1.2：长任务 Prompt Cache 利用率与前缀生命周期

目标：把 V2.1.1 从“短任务相对改善”推进到“长任务高利用率可复现”，并把应用可缓存性与
Provider 实际兑现率分开度量。目标不是靠加长 system prompt、重复相同请求或增加无意义回合
刷到一个高百分比，而是在任务正确性和总 prompt 基本不退化的前提下，使自然增长的多轮上下文
稳定复用。

进入条件：`stable-v2.1.1` 已验收。V2.1.1 的 tag 与 acceptance 不重写；V2.1.2 使用新版本、
新分支和独立证据目录。该阶段先于 V2.2，因为 working memory 的插入位置和更新频率会直接改变
缓存前缀，先做 memory 再修前缀生命周期会使两套验收都需要重跑。

根因假设必须分别验证：

1. 短任务效应：每个 namespace 的首请求必然冷启动，4–5 请求的 C02 对全链路命中率有明显
   上限；稳定 system/goal 占比高本身不是坏事，缓存建立后反而应提高可复用比例。
2. Provider/传输效应：当前 SiliconFlow 公共 API 的命中兑现有明显轮间和请求间波动。需要
   对比复用 HTTP client/keep-alive、请求间缓存落盘时间和同一前缀路由；未被 Provider 文档
   支持的 cache key、显式 breakpoint 或预热参数不得擅自发送。
3. 应用生命周期效应：当前 `append` 在滑窗移动前保持严格追加，但默认
   `recent_turns=6`；第 7 个 trajectory step 进入后会逐轮丢弃最老消息，使完整动态前缀失效。
   compaction 若每回合改写 `state_summary`，也会从 summary 位置开始反复打断缓存。

实施阶段：

1. **M1 可缓存性仪表盘**：每个请求记录 `prefix_epoch`、`cold_start`、相邻请求完整消息
   prefix/LCP、`prefix_reset_reason`、理论可复用 token、实际 hit/miss、Provider 经验粒度和
   `cache_capture_efficiency = actual_hit / theoretical_cacheable`。理论值必须标注 tokenizer、
   request-boundary/output-boundary 和块取整假设；无法精确计算时不得伪装成精确 token。
2. **M2 传输与 Provider 隔离实验**：在同一模型、API key、温度和 payload 下比较“每次新建
   client”与“run 级持久 client”，再用 0/2/5 秒 settle 仅做诊断，报告总 wall time。只有
   miss token、成本和端到端延迟的综合结果更好时才允许引入等待；不得用重试失败请求或重复
   相同请求制造命中。若当前模型无法稳定兑现，再对同一 Provider 中明确支持缓存的模型做
   能力探针，但不得把换模型结果混入当前模型 A/B。
3. **M3 cache epoch 布局**：在一个 epoch 内保留单调追加的 action/observation 消息，不因
   `recent_turns` 的滚动视图逐回合删除头部。接近上下文预算时一次性批量压缩到目标水位，
   生成不可变 summary checkpoint 并开启新 epoch；使用 hysteresis 避免以后每回合压缩和
   prefix reset。逻辑 working set 与 Provider 传输前缀分开建模，但关键事实保留率仍须 100%。
4. **M4 P1/P2 A/B**：P1 为 V2.1.1 当前 append，P2 为 epoch append。保留 C02 作为短任务
   回归；新增恰好 12 个不同动态后缀的固定长序列，以及自然需要恰好 9 次请求的真实长任务
   C07。C07 必须沿 artifact→contract→binding test→source 的依赖链，用独立工具回合核对
   contract、tests 与 source，不能通过跳过测试证据缩短 workload。默认 `recent_turns=6`
   时，第 8 个请求才首次包含第 7 个 trajectory step 并使 P1 滑窗移动；第 8 个请求锁定为
   全量 release check，第 9 个请求锁定为 final。固定序列中的长前缀和 C07 的稳定任务契约
   必须来自真实代码、日志或调查约束，不得使用无语义 padding。
5. **M5 正式验收与归档**：连续两轮使用独立 namespace，倒置 P1/P2 顺序，固定 Provider、
   model、temperature、预算和动态输入顺序；每个真实 workload 各运行 3 次，正式报告只引用
   入选证据，开发试跑不复制进 acceptance。

正式门禁：

- 主指标为包含每个 namespace/epoch 首次冷请求、零命中和 eviction 的**全链路 token 加权
  命中率**；固定长序列与真实长任务在两轮中均须达到 `>= 70%`。
- 固定探针的稳态区间使用预先锁定的前 2 个 warm-up 请求；真实任务的稳态从 Provider 首次
  报告非零 cache hit 的请求开始，并把其后所有请求（包括后续零命中、reset 和 eviction）
  全部计入。该指标两轮均须达到 `>= 80%`；它用于排除实际缓存尚未建立的 warm-up，不得用来
  替代未达标的全链路主指标，也不得删除稳态区间内的不利请求。
- `cache_capture_efficiency` 两轮均须达到 `>= 85%`。若预先锁定 workload 后测得其理论
  全链路上限 `< 80%`，该 workload 不具备证明 70%–80% 目标的资格，必须更换为自然长上下文
  case，而不是 padding、增加无意义回合或删掉低命中请求。
- P2 的真实任务通过率和关键事实保留率均为 100%；固定序列和 C07 的总 prompt tokens
  相对 P1 均不得膨胀超过 10%。固定序列全链路 uncached tokens 通常至少下降 40%；若 P1
  全链路命中率已经 `>= 80%`，相对降幅会受到数学上限扭曲，此时允许使用饱和替代门槛，但
  必须同时满足 P2 全链路 `>= 80%`、稳态 `>= 90%` 且绝对 miss 不高于 P1。C07 由于前
  7 个请求的 P1/P2 消息布局按定义相同，全链路 miss reduction 只作诊断；从第 8 个请求开始
  的 post-slide uncached tokens 必须至少下降 40%。Provider 重试不得超过配置，必须逐请求
  记录原因，并用保守物理口径核算：`attempt_count=N` 的逻辑请求以 `N × 最终 prompt` 计入
  prompt/miss/cost，cache hit 强制记 0，理论可缓存机会同样按 N 倍计；不得用失败 attempt
  的自预热制造命中收益。
- C02 仍须 P2 3/3 PASS，但不为该短任务设置 70% 的绝对门槛。为消除单轮模型随机多一个
  工具动作对总 token 的放大，prompt/miss 回归在两轮倒序证据上合并计算，分别不得膨胀超过
  10%/15%；不得挑选单轮或单次 attempt。
- P1/P2 的每个 C07 run 都必须有严格递增的 1–9 request index，并通过锁定 spec SHA-256 的
  8-step bash action shape：初始测试、指定 artifact grep、contract/tests/source 三次独立
  读取、独立 source edit、focused/full 验证。每侧 3 个 run 都必须产生恰好 6 个第 8 请求起
  的 post-slide 样本；请求数、断言定义或语义阶段不配对时不得计算为有效改善。
- 报告必须同时给出每请求序列、全链路/稳态命中及其区间口径、理论上限、兑现率、prefix
  reset 原因、compaction epoch、端到端延迟和费用估算；只报告一个聚合百分比视为验收失败。
- 两轮必须锁定同一干净 Git commit、Provider/model/temperature/预算、Docker digest 和动态
  输入，并固定 `recent_turns=6`、`max_prompt_chars=120000` 和 deterministic compaction；
  使用独立 namespace 并由时间戳证明倒置后的真实执行顺序。固定探针必须逐请求核验 payload
  SHA-256，并锁定 2 次 warm-up 与 10 次 steady-state；suite 只允许 C02/C07 精确矩阵且
  顶层/case 均 PASS。正式 eval 必须锁定 canonical suite 路径，并把 `case.yaml` 定义摘要、
  agent 修改前的 fixture 路径+内容摘要及来源路径闭环到 workspace/run/suite/聚合证据；C02/C07
  在 P1/P2、两轮和全部 attempts 中必须使用同一 authority profile，并逐文件与声明 Git commit
  的 tree object 对照。已哈希 trace 的逐请求 rows 必须固化进 suite report，聚合不得重新读取
  活 trace。最终归档 manifest 必须校验汇总报告及八份源证据的 SHA-256；八份入选
  report/manifest（suite report 已携带逐请求证据）合并为单个可搬运 evidence bundle，开发预检
  不得混入 acceptance。

验收归档（2026-08-02）：实现提交
`de3898ed54431f45cca9c83535bee2a5c5529b4e` 上完成 `formal-v212-round-81`（P1-first）与
`formal-v212-round-82`（P2-first）两轮独立正式验收。P2 固定长序列 full-chain 命中率为
87.65%/84.82%，steady-state 为 94.00%/91.53%；C07 full-chain 为 75.89%/74.73%，
steady-state 为 83.09%/81.84%，post-slide miss 相对 P1 分别下降 86.11%/81.63%。两轮
C02/C07 均为 3/3 PASS，全部 C07 run 均通过哈希 trace 动作回放。最终归档位于
`acceptance/stable-v2.1.2/`，仅保留 report JSON/Markdown、单一 evidence bundle 和 manifest。

失败回退：任何绝对命中门禁未通过时保留 `stable-v2.1.1`，不得创建 `stable-v2.1.2` tag，
高利用率能力继续标记为 experimental。默认不进入 V2.2；若确认瓶颈属于当前 Provider 公共池
且应用侧兑现率已达门禁，必须先形成明确的 Provider/模型选型决定并修改路线图，不能静默跳过。
通过后归档 `acceptance/stable-v2.1.2/` 并标记 `stable-v2.1.2`。

### V2.2：分层记忆与 Follow-up

目标：证明记忆能够减少重复 I/O，而不是只证明文件被写入。

进入条件：`stable-v2.1.2` 已验收。

先做 1 个两阶段 follow-up task，各配置运行 3 次；成功后扩到 3 个，最后才允许扩到 12 个 memory dependency task。

开发入口（2026-08-02）：代码版本进入 `2.2.0.dev0`，从 `stable-v2.1.2` 建立独立开发分支。
首个 `M01_service_contract_follow_up` 使用同一 source run 配对 M0（不注入）与 M1（显式来源 run）;
working memory 只接受相对 workspace 文件的有限行区间，由 Harness 保存原文、文件哈希和项目初始快照哈希，
新 run 不做环境式自动检索。项目快照不一致、记录完整性失败、路径越界或行区间无效时拒绝绑定。
开发评测入口为 `minicc memory-eval eval_cases/memory_suite_v1/M01_service_contract_follow_up --repeat 3
--execution-order alternating`；报告保存在不可覆盖的 `.minicc/suites/<suite-id>/`，不会直接写入正式
`acceptance/`。只有 M01 连续配对通过并确认每次 M1 读取数均低于 M0 后，才扩到 3 个 case。

首个门禁结果（2026-08-02）：`M01` 在 `d2fb860` 上完成 3 次 source/M0/M1 配对，9 个 run
全部 PASS，M0/M1 follow-up 关键事实正确率均为 3/3；每一对合同重复读取均为 `1 -> 0`，
聚合 prompt token 为 `12836 -> 8822`，旧 run 串入、无关注入和完整性无效记忆采纳均为 0，
Provider error/retry/protocol error 也均为 0。开发证据位于
`.minicc/suites/suite-20260802-110112-7f1c8d23/`，不计入正式 acceptance。满足首个门后，
允许新增 `M02_deploy_cli_follow_up` 与 `M03_validator_contract_follow_up`，形成 3-case 开发集；
在两个新增 case 各完成 3 次配对前，不进入 12-task 扩展。

三 case 开发门结果（2026-08-02）：M01/M02/M03 的三个独立 suite 共 27 个 run 全部为
`completed/PASS`，M0/M1 follow-up 关键事实正确率均为 9/9，每一对来源文件重复读取均为
`1 -> 0`，聚合为 `9 -> 0`；follow-up prompt token 聚合为 `38616 -> 26642`（下降约
31.0%）。旧 run 串入、无关注入、完整性无效记忆采纳、provider error、protocol error、审批和
policy deny 均为 0。M03 的 M0 第 2 轮有 1 个请求在两次 transport/protocol 异常后由既定重试
机制成功完成，前序 run 未被废弃。开发 suite 为 `suite-20260802-110112-7f1c8d23`、
`suite-20260802-110720-abc257ab`、`suite-20260802-111049-617b63a0`；它们绑定两个开发提交，
因此只证明 3-case 开发门通过，不作为正式 V2.2 acceptance。进入正式归档前必须在同一个最终
commit 上通过 release gate 并重跑，不能直接拼接这三份开发报告。

正式验收协议：M01/M02/M03 必须在同一个干净 Git commit 上分别以 Docker、固定摘要镜像、
`--repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate` 运行。
release gate 会在运行前后校验 Git 状态和 canonical case/fixture authority；聚合器再逐项校验
3 份 suite manifest、27 个 run artifact manifest、state/trace/metrics/eval 结果、模型身份、
来源 run 绑定与读取命令。正式归档固定为 `acceptance/stable-v2.2/`，且只允许包含
`report.json`、`report.md`、`evidence.json`、`manifest.json` 四个文件；原始 run/suite 继续留在
被忽略的 `.minicc/`，由 `evidence.json` 封装入选 suite 的报告和 manifest，避免把临时产物复制
进 Git。

```bash
uv run minicc memory-eval eval_cases/memory_suite_v1/M01_service_contract_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-eval eval_cases/memory_suite_v1/M02_deploy_cli_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-eval eval_cases/memory_suite_v1/M03_validator_contract_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-report --report <M01-report.json> --report <M02-report.json> --report <M03-report.json> --output-dir acceptance/stable-v2.2
```

验收标准：

- short-term、long-term、working memory 的所有权和生命周期有明确测试。
- Follow-up 阶段关键事实回答正确率为 100%。
- 开启记忆后重复文件读取次数相对无记忆基线稳定下降。
- 旧 run 记忆串入当前 run 的次数为 0。
- 无关记忆注入率和错误记忆采纳率均为 0。
- 报告给出原始命令、trace 证据、读取次数和 prompt 成本。
- 只有实际测得时，才允许写“12 个任务从 N 次降到 M 次”，不得预设 `60 -> 0`。

正式验收结果（2026-08-02）：M01/M02/M03 分别生成
`suite-20260802-130812-5862115e`、`suite-20260802-131105-3763ea38`、
`suite-20260802-131409-441c511f`，全部绑定共同执行提交
`15fadae08d7d424853ba24b4dca534501493a183`。三组共 27 个 run 全部为
`completed/PASS`，M0/M1 follow-up 关键事实正确率均为 9/9，每对重复来源读取均下降，聚合
`9 -> 0`；follow-up prompt token 为 `36878 -> 26617`（下降 27.82%）。旧 run 串入、
无关注入、完整性无效采纳、provider error/retry、protocol error 和审批均为 0。首次聚合暴露
读取器错误地要求原始 `eval_result.json` 包含派生字段 `formal_metric_eligible`；修复提交
`ba5ac0cdb5003dc9a029943f5469820f6a31a5e0` 改为使用既有 ledger 从完整证据重新计算资格，
没有改变 runner、case、prompt 或正式 run，因而复用三份已通过 suite 而未重复消耗 Provider。
最终四文件归档位于 `acceptance/stable-v2.2/`。

失败回退：回到 `stable-v2.1.2`，working memory 降级为 experimental。通过后标记
`stable-v2.2`。

### V3.0：评测闭环与简历发布版

目标：形成可演示、可复跑、可用于简历陈述的发布版本。

进入条件：V1.2、V2.0、V2.0.1、V2.0.2、V2.1 已通过；V2.2 可选择性通过，未通过的能力必须
明确标为 experimental。

开发入口（2026-08-02）：从 `stable-v2.2` 建立 `codex/stable-v3.0`，版本进入
`3.0.0.dev0`。新增 `minicc release-report`，默认聚合 Stable V1.3 系统回归、V2.1 Context、
V2.2 Memory、V2.0 Resume 四维证据，输出不可覆盖的 JSON/Markdown/CSV/manifest。每条 claim
必须携带 case/suite/run ID、配置、source SHA-256、原始 artifact 和复跑命令；缺失维度显示
`EMPTY/experimental`，不能写成稳定能力。首个开发报告为
`.minicc/release-reports/v3-development-first/`，四维分别定位 15/24/27/1 个正式 run，开发门为
PASS，但不作为 V3.0 acceptance。

正式协议：先在同一个干净提交上运行 canonical C01/C02/C03/C04/C09 各 3 次；release gate
锁定 `eval_cases/capability_suite_v1`、Docker 摘要镜像、case/fixture Git authority 和执行前后
Git 状态。再由 `release-report --release-gate` 校验 15 个新系统 run 的 formal metric 资格，并
复用已经验收的 Context/Memory/Resume 证据，最终写入 `acceptance/stable-v3.0/`。

```powershell
uv run minicc eval eval_cases/capability_suite_v1 --case C01_repo_onboarding --case C02_fix_failing_test --case C03_add_cli_option --case C04_add_regression_test --case C09_hitl_destructive_command --repeat 3 --milestone v3.0-acceptance --release-gate
uv run minicc release-report --system-report <formal-system-suite-report.json> --output-dir acceptance/stable-v3.0 --release-gate
```

正式验收结果（2026-08-02）：首轮 `suite-20260802-144947-04ab8617` 完整结束但为 FAIL，暴露
AskAction 审批计数遗漏与最后 1 turn 的收敛提示歧义；修复没有提高 case 预算或放宽断言。最终
执行提交 `7d346fb77a191f0a5dbbb3157419cd0c0079c0cf` 上的
`suite-20260802-150630-4df523ea` 达到 C01/C02/C03/C04/C09 各 3/3、合计 15/15 PASS，
Provider error/retry 和 protocol error 均为 0，3 个 C09 run 均按预期进入 `waiting_approval`。
验证提交 `cc150b0ae815e2add2f4ac036b3e0371205ddda4` 使用既有 ledger 统一复核 HITL 正式指标
资格，并把执行/验证提交间仅有的 `src/minicc/cli.py`、`tests/test_cli.py` 差异写入报告。最终
四维聚合覆盖系统 15、Context 24、Memory 27、Resume 1 个 run，全部为 `stable/PASS`；
`acceptance/stable-v3.0/` 恰好包含 report JSON/Markdown/CSV 和 manifest 四个文件。

验收标准：

- README 首屏提供一条 10 分钟内可完成的演示路径。
- 固定 benchmark 能一条命令运行并生成 JSON、Markdown 和 CSV。
- 报告至少覆盖系统回归、上下文治理、记忆收益、断点恢复四个维度；未启用维度显示 empty/experimental，不伪造结果。
- 每个简历数字都有 case、run id、配置、原始 artifact 和复跑命令。
- 必须交付 `docs/ETCLOVG_CAPABILITY_MATRIX.md`，逐层记录能力声明、代码入口、测试、验收命令、
  run id、原始证据、当前状态和已知边界。
- ETCLOVG 矩阵中的状态只允许使用 `stable`、`experimental`、`not implemented`；只有已经通过
  版本验收且能从 Viewer 或验收归档定位原始 run 的能力才允许标记为 `stable`。
- V2.2 未通过时不阻塞 V3.0，但 Memory 层必须如实标记为 `experimental`，并保留对应的
  后续验收版本和复跑入口，不能用已有基础实现代替收益证据。
- 新机器按照 runbook 可以完成安装、单 case 运行和报告查看。
- Web Viewer 缺少可选 artifact 时不崩溃。

失败回退：回到最近已验收 tag，只发布已经被证明的能力。通过后标记 `stable-v3.0`。

### V3.x：可选研究版本

Runtime tool synthesis 和 meta review 不属于 V3.0 的必需项。每次只能选择其中一个方向，并使用独立的 `experimental/*` 分支。

V3.1 开发入口（2026-08-02）：从 `stable-v3.0@908e8a3` 建立
`experimental/meta-review`，只选择 Meta Review，不同时开发 runtime tools。审查必须由命令
显式触发，只读 `state/metrics/trace/run_report/diff` 并在调用模型前后复核来源哈希；结果写入
独立 `.minicc/meta-reviews/`，不追加源 trace、不改变原 run verdict、不自动采纳建议。正式 A/B
固定使用同一提交、模型、case authority 和 Docker 摘要运行 C02：A0 与 A1 各 3 次，A1 的三个
run 各生成一份 `used_model=true` 的审查。聚合门要求来源完整性、run-review 一一对应、实际
模型调用、A1 通过率不低于 A0，并明确只证明可运行性与非回归，不宣称建议质量提升。

正式实验验收结果（2026-08-04）：执行提交
`263785855e6fa0bd845b9143cd84b338193f00fd` 上，A0
`suite-20260802-171416-ad22a9cc` 与 A1 `suite-20260802-171631-8353bcda`
均为 C02 3/3 PASS，六个 run 全部 `completed`，A1 通过率未低于 A0。审查提交
`29eaad9be1aa1e30705cf1e806ec8ef94e801fea` 对 A1 三个 run 各生成一份
`used_model=true` 的独立 review，模型调用次数均为 1、schema 重试均为 0、总 token 分别为
21595/19706/19472；Provider timeout 重试分别为 1/0/2，最终全部成功。聚合首次暴露通用 suite
读取器错误地对非缓存实验要求 `cache-experiment/None` namespace；验证提交
`734f413d24b0aed500a020d74a5248310406eba7` 只在存在 `cache_sequence_id` 时复核该字段，并复用
既有六个 run 和三份 review。首轮 18 项门禁全部通过，全量回归为 305 passed，当时结论保持
experimental：只证明显式离线审查可运行、源证据哈希不变及固定 case 非回归，不证明建议质量。
该阶段四文件归档已在后续 Stable V3.1 归档形成后删除，避免 acceptance 同时保留两套重叠口径；
历史仍可由提交 `2729f39d993be474baa872d42afcfb770694eb4b` 恢复。

Stable 升格补充验收（2026-08-12）：为消除“模型输出了建议但无法证明建议有依据”的缺口，
提交 `cf58d5c23ec03db71a0920dff06a3d73ab2d047d` 引入 Meta Review schema v2，强制 finding 与
suggestion 使用唯一 ID、每个证据路径能从不可变 snapshot 重新解析、每个 finding 至少关联一个
带预期效果和确定性验证方法的建议；提交 `b7a541d2a924ddd3d0da0f010c07c1469cb32731`
补齐合法嵌套 trace 路径的逐层验证。复用原 A1 三个 run 重新生成三份 review，得到 11 条 finding、
11 条关联建议和 21 个可解析证据引用；前两份一次通过，第三份经 1 次 schema 纠错通过，三份均无
Provider transport retry。最终 A0/A1 仍为 3/3 与 3/3 PASS，20 项聚合门全部通过，全量回归为
307 passed，正式四文件归档位于 `acceptance/stable-v3.1/`。因此离线 Meta Review 升格 stable；
声明边界仍不包含“采纳这些建议一定提高下游任务质量”，且建议不会被自动写入 prompt、memory
或代码。

### V3.1.1：工程质量与发布治理补丁

目标：不改变 Stable V3.1 的能力声明和正式 Provider 实验口径，为当前稳定基线增加可自动执行的
工程质量门，并消除版本文档漂移。

工作范围：

- GitHub Actions 在 Python 3.11/3.12 上执行锁文件安装、Ruff、mypy、pytest coverage 和 package build。
- 全包分支覆盖率低于 78% 时失败；lint、类型检查、测试或构建任一失败时不得发布。建门实测
  基线为 78.60%，后续 80% 目标只能通过补测试达到，禁止排除低覆盖模块美化数字。
- 补齐 changelog、贡献指南和安全报告流程；README 只保留一个当前版本口径。
- 不修改 `acceptance/stable-v3.0/`、`acceptance/stable-v3.1/` 或任何既有 run/suite 原始证据。

验收标准：

- `uv sync --locked --all-groups`、`uv run ruff check src tests`、`uv run mypy src/minicc` 全部通过。
- `uv run pytest --cov=minicc --cov-report=term-missing --cov-report=xml` 全部通过且全包分支覆盖率不低于 78%。
- `uv build`、`git diff --check` 通过，工作区仅包含解释过的工程治理变更。
- 完成后只生成一个正式提交；验收通过后创建 annotated `stable-v3.1.1` tag。

本补丁不需要重新调用 Provider；Stable V3.1 Meta Review 与 V3.0 四维能力结论继续引用原正式归档。

### V3.2：目标相关 Skill/Feedback 指引选择

目标：把现有“展示全部 Skill catalog + 读取本机可变反馈文件”收敛为可审计、可绑定、可做 A/B 的
相关指引选择能力。本阶段不实现自动反馈提取、环境式检索或 RAG。

进入条件：`stable-v3.1.1` 工程质量补丁已通过并发布；开发从该 tag 建立
`experimental/skill-feedback-memory`。

实现范围：

- Skill 仅按 goal 与 name/description 的确定性词项重合选择，排序和上限固定，拒绝 symlink skill。
- 正式 A1 的 Feedback rules 必须来自 eval workspace 中的 `guidance/feedback_rules.jsonl`，由 case
  authority 与 Git commit 共同绑定；不得读取本机 ambient `.minicc` 作为正式证据。
- 每个 run 在 metrics/trace 中记录 skill 名称、feedback rule ID 和唯一 selection event。
- `guidance-report` 只聚合完整、不可变且同提交的 A0/A1 suite，并输出四文件报告。

正式协议：

```powershell
uv run minicc eval eval_cases/guidance_suite_v1 --case G01_release_manifest_guidance --repeat 3 --guidance-variant a0 --guidance-sequence-id <round> --guidance-execution-order <a0-first|a1-first> --milestone v3.2-guidance-acceptance --release-gate
uv run minicc eval eval_cases/guidance_suite_v1 --case G01_release_manifest_guidance --repeat 3 --guidance-variant a1 --guidance-sequence-id <same-round> --guidance-execution-order <same-order> --milestone v3.2-guidance-acceptance --release-gate
uv run minicc guidance-report --disabled-suite <a0-report.json> --enabled-suite <a1-report.json> --output-dir acceptance/stable-v3.2 --release-gate
```

验收标准：

- A0/A1 各 3 个独立真实模型 run，绑定同一实现提交、case authority、模型、温度和 Docker 摘要。
- A0 三次均不选择 skill/rule；A1 三次均且只选择 `release-manifest` 与 `release-legacy-id`，干扰项为 0。
- A1 3/3 PASS 且通过率不低于 A0；两个 arm 均无 Provider/protocol failure。
- A1 三次合计 Bash 动作至少比 A0 少 3 个，且总 prompt tokens 低于 A0；只证明该固定 case 的
  指引收益，不外推到其他任务。
- 全量工程门继续通过，最终 acceptance 只保留 report JSON/Markdown/CSV/manifest 四个文件。

通过只允许声明“canonical case 上的相关指引精确选择与任务非回归”。自动规则提取、长期记忆、
跨 case 泛化或任务质量提升必须另开后续版本验证，不能由本结果外推。

Stable 升格验收（2026-08-12）：执行提交
`178d3ed142b1d492c741539685cb13b51aa075f0` 上的 A0 suite
`suite-20260812-145957-8735fe6b` 与 A1 suite `suite-20260812-150155-3b650c49` 均为
3/3 PASS，无 Provider/protocol failure。A0 三次选择均为空；A1 三次精确选择
`release-manifest` 与 `release-legacy-id`，无干扰项。A1 Bash 动作总数从 13 降至 6，prompt
tokens 从 16,683 降至 8,162，全部收益门通过。最终归档只保留
`acceptance/stable-v3.2/` 下四个报告文件，声明边界维持不变。

### V3.3：真实仓库演示与验证闭环

状态：`completed`（开发验收）。M1/M2/M3 已实现并完成确定性回归；M4 已完成真实仓库演示与证据归档。
该结果仍不属于 Stable V3.2 的通用能力声明。

目标：把现有 Harness 能力落到一个真实外部仓库任务上，形成可复现的“项目理解 -> 修改 ->
运行验证 -> 证据归档”演示。重点是证明已实现的工程机制确实有效，不把“模型相对裸跑的通用
能力提升”作为主线发布条件。

当前真实仓库预演记录（2026-08-18）：固定源仓库 `D:\Code\MyHeiMaDianPing`、Provider
`deepseek-ai/DeepSeek-V4-Flash`、任务 `CacheDeleteMessage` 重试退避边界、Gate 命令
`mvn -q -Dtest=CacheDeleteMessageTest test`。首轮 `20260817-235501-ec4a3027` 因
Windows Bash 的 `JAVA_HOME`/UTF-8 基础设施问题失败；修复执行器后，
`20260818-000100-5f0ff300`（9 turns、8 actions、Gate 1/1）和
`20260818-001529-ceb0129e`（6 turns、5 actions、Gate 1/1）通过。两次通过 run 的源仓库
摘要均保持不变，最终 diff 均仅包含 DTO 与单元测试两个文件；随后第三次修复后 run
`20260818-073342-6124bae6`（8 turns、7 actions、Gate 1/1）也通过。三次修复后 run
均保留 state、trace、metrics、workspace manifest、diff 和 report，且源仓库摘要未改变；
首轮基础设施失败也作为初始失败证据保留在 `20260817-235501-ec4a3027`，没有删除或重跑覆盖。

本版本只新增两项直接服务于该目标的运行时能力：Real-repository Onboarding 和 Runtime
Completion Verification Gate。C02 继续作为 smoke test，C07 继续作为 artifact/cache 组件回归，
G01 继续作为相关指引效率实验；三者不能替代真实仓库演示，但仍然保留为已有能力的回归证据。

#### V3.3-M0：能力口径与 A/B 合同冻结

- 修正文档与源码漂移：在真实实现并验收前，不得声称存在 `project_guide.py`、自动读取
  `MINICC.md`、自动识别构建系统或运行时完成验证。
- 在代码和报告中区分 `model_final_requested`、`verification_rejected`、`completed` 和
  `failed`；模型输出 `final` 不再天然等价于任务成功。
- 定义可复现的 `minimal`/`full` Harness profile，并把所有 feature flag、Provider 返回的模型
  身份、温度、预算、sandbox 镜像摘要和源码快照哈希写入 state、trace、metrics 与 suite report。
- A0 `minimal` 仍保留相同的 Provider、`bash/ask/final` 协议、Bash executor、workspace 副本、
  PolicyChain 和 Observation 合同。不得用“没有工具的单轮问答”充当 Baseline。
- A1 `full` 在 A0 基础上启用项目画像、项目指南、现有 Context 治理和 Completion Gate。正式报告
  必须列出两侧唯一差异，禁止使用未记录的 ambient memory、人工提示或运行中干预。

#### V3.3-M1：Project Context / Real-repository Onboarding

- 新增有界、确定性的 Repository Inspector，在 run workspace 副本中识别 Git 状态、语言、
  `pom.xml`/Gradle/Python/Node 构建入口、候选测试命令、模块边界和外部服务前置条件；不得遍历
  `.git`、依赖缓存、构建产物或敏感目录。
- 新增可选 Project Guide Loader，只读取 workspace 根目录中显式存在的 `MINICC.md`，限制大小，
  拒绝 symlink，并记录相对路径、内容 SHA-256、截断状态和注入事件。项目指南可以描述通用构建和
  约束，但不得包含正式任务的目标文件、补丁、答案常量或隐藏断言。
- 生成不可变 `repository_profile.json`，每个字段携带来源文件或探测依据；画像不确定时标记
  `unknown`，不得让模型生成的猜测伪装成 Inspector 事实。
- Repository Inspector 是 Context 的输入生产者，ContextBuilder 仍负责预算、布局、压缩和注入；
  不把仓库扫描、Maven/Gradle 解析硬编码进 ContextBuilder。
- 同一源码快照重复生成的 profile 必须字节稳定；Inspector 自身不得修改 workspace，且在 Windows、
  WSL/Docker 路径差异下输出相同的仓库相对事实。

#### V3.3-M2：Runtime Completion Verification Gate

- 为 Agent Loop 注入 `CompletionVerifier` 接口。未配置代码任务 Verifier 时使用显式
  `NoopCompletionVerifier` 保持普通 `run` 行为兼容；不得把 Maven、pytest 或某个 case 写死在循环。
- 模型请求 `final` 时，Gate 在同一隔离 workspace 和相同 Policy/Trace 边界内运行预先声明的
  Verifier。通过后才允许 `completed`；失败时生成结构化 observation，追加到 trajectory，并让模型
  在剩余预算内继续修复。
- Verifier 命令及超时必须在 run 开始前绑定并哈希；模型、Skill、Project Guide 和被测仓库均不能
  修改该合同。禁止联网安装依赖、跳过测试、删除测试或用退出码包装伪造 PASS。
- Gate 设置独立的最大尝试次数和总耗时；每次记录命令、exit code、stdout/stderr artifact、触发
  `final` 的 turn、失败分类和最终 verdict。Verifier 基础设施错误与任务失败分开统计。
- Eval 的事后 assertions 继续作为独立裁判，不直接复用 Gate 自报的 verdict。正式 PASS 要求运行时
  Gate 与事后 assertions 一致；不一致时 fail closed。

#### V3.3-M3：外部仓库只读快照与任务冻结

- 外部源仓库只允许作为快照来源。运行前记录 resolved path、Git commit、dirty/untracked 清单和
  内容摘要，复制到 `.minicc/runs/<run-id>/workspace` 后才允许 Agent、Inspector 或 Verifier 执行。
- 每个 run 前后复核原始仓库 Git 状态和内容摘要完全一致；任何源仓库变化都使整个 suite
  infrastructure FAIL，不得计入通过率。正式流程不提供 `--no-workspace-copy`。
- 首轮选择 3 个真实、可解释的 Spring Boot 任务，每个任务修改范围控制在 1-5 个文件，Verifier
  在预热依赖后应于 2-5 分钟内完成。任务不得依赖未受控的远程 MySQL、Redis、Kafka 或网络服务。
- 任务优先覆盖：测试环境隔离、多文件业务缺陷、边界回归测试补全。具体任务由执行阶段根据真实
  失败选择，但必须在正式 A/B 前冻结 prompt、source commit、writable paths、Verifier、预算和
  case hash。
- 允许使用 2 个任务调试 Harness；至少 1 个 holdout 任务在冻结后不得根据 A0/A1 输出修改 Project
  Guide、Skill、Verifier 或预算。隐藏断言只约束正确性和防作弊，不得要求唯一实现细节。

#### V3.3-M4：真实仓库演示与证据归档

- 选择 1 个真实、可解释的 Spring Boot 任务，优先覆盖测试环境隔离、多文件业务缺陷或边界回归
  测试补全；任务修改范围控制在 1-5 个文件，Verifier 在依赖预热后应于 2-5 分钟内完成。
- 同一冻结 commit 至少连续执行 3 次真实模型 run；每次都必须保留 state、trace、metrics、diff、
  workspace manifest、Verifier artifact 和最终 report。三次运行不能依赖人工修改或中途提示。
- 演示必须展示一次初始失败、模型行动轨迹、实际 diff、Runtime Gate 结果和独立事后 assertions；
  不能只展示最终代码或模型的 final 文本。
- 通过条件为：3 次任务均完成，三次原始仓库内容和 Git 状态均未改变，Runtime Gate 与事后
  assertions 结论一致，且不存在未解释的 Provider/protocol/policy/infrastructure failure。
- 主线只声明“在固定仓库、固定 commit、指定 Provider 和固定任务上完成可复现演示”，不声明任意
  Spring Boot 仓库成功率，也不声明模型通用能力提升。

#### V3.3-Optional：Capability Lift A/B 研究轨道

- 在 M4 主线通过后，才允许选择 2-3 个冻结任务做最小 Loop / 完整 Harness 对照；该实验不阻塞
  V3.3 主线，不影响 Stable 工程能力声明。
- A0/A1 必须绑定同一 Provider、任务、预算、sandbox 和 source commit；A0 仍保留 Bash、workspace
  copy、PolicyChain 和 Observation，不得用无工具单轮问答充当 Baseline。
- 只报告固定任务上的通过率、错误 final、Gate 拒绝后恢复、动作数和 token；不外推到其他模型、
  任意仓库或通用智能。
- 若 A1 只减少 Bash/token 而成功率没有提升，结果仍可作为效率实验归档，但不写成 Capability Lift。
- 正式运行开始后禁止提高预算、放宽断言、向 Skill/Project Guide 加入任务答案或删除失败 run；
  任何合同变化必须使用新 case hash、suite id 和完整的新一轮 A/B。

#### V3.3 验收与声明边界

- 确定性测试覆盖 profile 生成、Project Guide 安全读取、FinalAction Gate 通过/拒绝/重试、Verifier
  超时与基础设施错误、checkpoint/resume、源仓库零修改和 A/B 聚合资格。
- `ruff`、`mypy`、全量 pytest coverage、package build、V3.2 guidance、V3.0 固定矩阵和 V2.0
  checkpoint/resume 回归不得下降；V3.3 各子里程碑分开提交，避免一次同时改 Context、Loop 和 Eval。
- 正式归档固定输出 report JSON/Markdown/CSV、manifest 和自包含 evidence；每个数字必须能定位到
  task hash、run id、trace、metrics、diff、Verifier artifact 和复跑命令。
- 只有 M4 主线通过后，才允许声明“在固定仓库、固定 commit、指定 Provider 和固定任务上完成
  可复现的真实代码修改与测试验证”。M1/M2 的收益必须由对应 trace、metrics 和 Gate/Verifier
  结果支撑，不得用 prompt 文案或单次模型 final 代替。
- Optional A/B 只有在其自身对照门通过后，才允许增加作用域明确的 Capability Lift 声明；不得回退
  使用 C02、C07、G01 或历史 67 runs 包装真实仓库成功率。

### V3.4：最小真实项目评测闭环（已完成本地真实模型验收）

目标：把真实项目评测收敛为一个适合实习项目展示、面试解释和本地复跑的最小闭环。该阶段优先
证明“Agent 修改了代码，并且修改后的项目通过了独立验证”，不继续扩展为通用远程仓库平台。

#### 设计取舍

- 每次只运行一个 Agent、一个本地任务、一个临时 workspace。
- 任务使用小型 fixture 或从真实项目中抽取的最小文件集，不复制整个大型项目及其依赖缓存。
- workspace 由 `mkdtemp` 创建，任务结束后在 `finally` 中清理；日志、trace 和验证结果在清理前
  保存到 run artifact。
- Agent 的最终文本只作为过程记录，不能作为成功依据。
- 成功必须由评测代码重新执行预先声明的 `verify` 命令，并要求退出码为 0；必要时再增加少量
  文件完整性断言，例如测试文件不得被修改。
- 本阶段不引入 Git worktree、patch replay、远程 GitHub 拉取、跨任务缓存或复杂 workspace
  retention。它们属于未来需求，不能阻塞当前真实任务演示。

#### 单任务目录约定

每个评测任务只需要以下内容：

```text
<case>/
  case.yaml       # prompt、预算、setup、verify、允许的工作目录
  setup.ps1       # 可选：生成或准备初始项目
  verify.ps1      # 独立验证脚本，退出码 0 才算通过
  fixture/        # 小型项目文件，或从真实项目抽取的最小复现
```

`setup` 负责准备一个已知会失败的初始状态；运行器在 Agent 开始前先执行一次 `verify`，确认
任务确实是失败态，避免出现“任务本来就已经通过”的假阳性。

#### 运行流程

```text
创建临时 workspace
    -> materialize fixture / 执行 setup
    -> verify 必须先失败
    -> 启动单 Agent（cwd = workspace）
    -> Agent 请求结束或达到预算
    -> 运行器重新执行 verify
    -> 保存 trace、stdout/stderr、diff 和 verdict
    -> finally 清理临时 workspace
```

运行器至少区分以下结果：

- `passed`：Agent 结束后独立 `verify` 通过，且没有违反任务约束。
- `failed`：Agent 结束但 `verify` 仍失败，或文件约束不满足。
- `infrastructure_error`：setup、verify 基础设施、Provider 或清理流程本身出错，不能混入任务
  成功率。
- `timeout`：超过任务预算；仍保留 trace 和验证输出。

#### 第一批任务（已落地）

已实现 3 个小而真实的任务，不追求数量：

1. 修复一个已有失败测试的业务 bug。
2. 为一个已有模块补齐边界回归测试。
3. 给一个 CLI 或 service 增加小功能，并由已有测试验证行为。

当前 fixture 位于 `eval_cases/real_project_suite_v1/`：R01 覆盖缓存删除重试时序 bug，R02 覆盖
边界回归测试与 mutation 检查，R03 覆盖商铺搜索缓存 key 小功能。三个验证器均要求本地 Java
toolchain，缺失时返回基础设施错误，不伪造任务结果。

本地真实模型验收已完成：三个 case 各连续运行 3 次，共 9/9 `passed`，无 Provider error，9/9
workspace 清理成功。证据归档于 `acceptance/stable-v3.4/`，执行 stage 为 `development_precheck`；
由于当前任务验证器依赖 Windows Java 17，本轮没有宣称 Docker `release_gate`，也不改变历史 Stable
版本的正式口径。

任务应来自 `D:\Code\MyHeiMaDianPing` 等真实项目中的可隔离问题，但每个 case 只保留完成任务
所需的最小文件和命令。任务 prompt、初始失败证据、verify 命令和禁止修改文件在冻结后不得随
模型输出调整。

#### 验收标准

- 先用一个 case 跑通完整流程，再扩展到 2-3 个 case。
- 每个 case 至少连续运行 3 次真实模型，记录每次的 verdict、turns、actions、耗时和验证输出。
- 通过条件是独立 `verify` 通过，不接受 Agent 自报完成替代验证。
- 每次运行都保留最小可审计证据：run metadata、trace、verify stdout/stderr、diff 或变更文件
  列表、最终 verdict。
- 所有结束路径都执行清理；清理失败单独报告，不覆盖原始任务 verdict。
- 任务失败、验证失败、Provider/基础设施失败分开统计。
- 不改变已有 V3.0-V3.3 的回归口径；本阶段不宣称任意 GitHub 仓库成功率或通用 coding 能力提升。

#### 后续再考虑

只有 V3.4 的最小闭环稳定后，才讨论以下扩展：远程 GitHub source、完整仓库快照、并发任务、
workspace retention、patch replay、跨 run 比较和更复杂的 judge。任何扩展都必须有明确需求，
不能因为“业界有”就提前加入。

进入 Stable 的最低条件：

- 先有确定性单元和集成测试。
- 再有 1 个真实 case 连续 3 次通过。
- 功能开启后的通过率不低于关闭时。
- 指标证明实际使用，而不是只有配置开关或 prompt 文案。
- 不改变 V1.2 固定回归和 V2.0 resume 的结果。

### V3.5：稳定公开题库评测

目标：在 V3.4 的评测闭环上，只补齐三类能够稳定复跑的证据：6 个公开 Python 任务的固定回归、
同一批任务上的一组 Context A/B、checkpoint/resume 与 workspace drift 恢复评测。详细实施步骤固定在
[`docs/V3_5_PUBLIC_BENCHMARK_EXPERIMENT_PLAN.md`](docs/V3_5_PUBLIC_BENCHMARK_EXPERIMENT_PLAN.md)。

#### 已冻结的技术取舍

- 任务从 Aider Polyglot Benchmark 的 Exercism Python 候选池中预检后固定 6 个，首选 `wordy`、
  `scale-generator`、`variable-length-quantity`、`go-counting`、`simple-linked-list`、`rest-api`，
  备用候选为 `bowling`、`poker`、`sgf-parsing`；正式运行开始后不再替换。
- 只复制完成这 6 个 case 必需的题目说明、初始代码和测试，并记录上游 URL、commit、原始路径、
  SHA-256 和许可证。测试只放在 Agent 不可见的 `verifier/`。
- 不接入 Aider Agent，不复制 Aider 整套 benchmark runner，不实现失败后再次修改，也不增加第四类实验、
  通用导入框架或并发执行。
- 每个 case 从全新 workspace 独立运行 3 次。主要结果就是“最终隐藏 Verifier 通过次数 / 18 次 run”；
  同时报告预算内完成率、基础设施错误、turns、actions、耗时、token 和重复文件读取。
- SWE-bench、SWE-bench Lite、远程仓库安装和历史 `minicc swebench-lite` 适配不属于 V3.5。

#### V3.5-M0：任务与来源冻结

- 建立 `THIRD_PARTY_NOTICES.md`，记录 6 个任务的许可证与来源摘要。
- 固定 DeepSeek V4 Flash、temperature、预算、sandbox、执行顺序、3 次 repeat 和结果分母；`--execute-local`
  只用于开发预检，正式 18-run 使用现有 Docker sandbox。
- 在正式运行前只允许做一次单次校准；若 6 题明显过难或存在基础设施问题，先停下来修合同并重新冻结，
  不得在正式运行后根据失败删题或放宽测试。

#### V3.5-M1：固定 case 与隐藏 Verifier

- 手工、逐题把 6 个任务适配为现有 `case.yaml + fixture/ + verifier/` 结构，不建设通用导入系统。
- 先扩展 `case.py` 允许 `initial_verify.type=python_verifier`，再让 `runner.py` 在初始验证时传入
  `verifier_dir`；同一个哈希绑定的隐藏 Python Verifier 必须证明“初始代码失败、参考代码通过、Agent
  修改后重新独立验证”。
- 测试覆盖 verifier 路径逃逸、SHA-256 不一致、初始假阳性、参考代码失败和测试泄漏到 workspace。

#### V3.5-M2：固定回归

- 先对 6 个任务各跑 1 次校准并保留完整结果；校准不进入正式通过率。
- 冻结 6 个 case 的 definition、fixture、verifier、预算和执行顺序后，每题独立运行 3 次，共 18 次。
- 复用现有 `minicc eval` 的 JSON/Markdown/CSV/manifest，并新增一个只读的离线 aggregator 校验 18 个
  run、冻结 hash、分母和不可覆盖输出；不另写 benchmark runner 或报告平台。
- 正式结果必须保留全部成功、失败、timeout 和 infrastructure error，不能只挑成功 run。

#### V3.5-M3：一组 Context A/B

- 从 6 个冻结任务中选择 2 个确实会触发上下文预算的任务。A0/A1 使用相同模型、任务、预算、sandbox
  和执行顺序，唯一差异是现有 `--context-variant a0/a1`。
- 每臂每题独立运行 3 次，报告最终 Verifier 通过率、prompt tokens、压缩次数、关键事实保留率、文件读取、
  重复文件读取、turns 和 command failures。
- 若 A1 没有实际触发 compaction，则报告无效并停止；不再增加其他上下文实验补数字。

#### V3.5-M4：Resume / Drift 恢复评测

- 复用现有 `CheckpointManager` 和 `tests/test_checkpoint.py`，补成一份自动化恢复报告，不重写 checkpoint。
- 固定覆盖 8 个场景、9 个断言：修改前中断、修改后验证前中断、验证失败后中断、外部修改文件、外部
  删除文件、错误 run/workspace 绑定（两个独立断言）、Provider 短暂失败、已完成 action 不重复执行。
- 确定性场景全部通过后，选 1 个冻结公开任务做一次真实模型中断、恢复和最终隐藏 Verifier 验证。

#### V3.5-M5：验收与声明

- `acceptance/stable-v3.5/` 只保存固定回归、Context A/B、Recovery 三组报告及其 manifest/evidence 索引；
  复用现有报告格式，不建设新的统一聚合器。
- V3.5 完成条件为：18 次固定回归完整落盘、一组有效 Context A/B、8 个场景的 9 个恢复断言通过、1 次
  真实模型恢复有最终 Verifier 结果、第三方来源完整、旧版本回归不下降。
- 简历只使用报告实际生成的数字。任何百分比必须能追溯到 run id、trace、metrics、diff 和 Verifier。

#### V3.5 时间边界

- 按 M0 -> M1 -> M2 -> M3 -> M4 -> M5 顺序实施，每个阶段单独提交。
- 不新增上述范围以外的题型、实验变体或框架。任何新想法先记到 future work，不进入 V3.5。
- 每阶段先运行 focused pytest，最终再运行 `ruff check`、`mypy`、全量 pytest coverage 和 package build。

### V3.6：Hybrid FS/Shell Tools 与有界多工具调度（M0-M4 implementation archive）

目标：参考 DeepSeek Harness、PI、Claude Code 和 Codex 的共同结构，把文件读写从 Bash 字符串中拆出为 `read`、`edit`、`write`，保留 `bash` 作为通用 shell，并允许一个模型响应包含多个工具调用。详细设计合同见 [`docs/V3_6_HYBRID_TOOLING_DESIGN.md`](docs/V3_6_HYBRID_TOOLING_DESIGN.md)。

进入条件：

- Sandbox Runtime 生命周期治理的真实 Docker 集成测试已在可用 Docker daemon 上补跑并通过发布门；仅有 `353 passed` 的单元结果、跳过集成测试或“待 Docker Desktop 恢复”不满足进入条件。
- `stable-v3.5` tag 已完成并作为 V3.6 分支基线；V3.6 从该 tag 创建独立分支，不从夹带未验收 Sandbox Runtime 变更的工作区开工。
- 开工工作区不包含 Sandbox Runtime 生命周期治理的未提交变更；V3.6 失败归因不得同时混入 Sandbox Runtime、Context、Memory 和多工具调度修改。

上述进入条件已满足：`stable-v3.5` 已补标至已提交的 V3.5 内容，且真实 Docker 集成门禁已通过。
M0-M4 的实现与离线 evidence 已归档；M5 真实模型 A/B 仍需独立冻结 provider budget 后产生正式结论。

V3.6 不改写 V3.5 的公开题库、Context A/B 和 Recovery 结论；它新增两个显式 profile：

- `baseline-bash`：现有单调用 `bash/skill/ask/final`，用于回退和对照；
- `hybrid-v3.6`：`read/edit/write/bash` 加上 `ask/skill/final`，使用有界多调用调度。

#### V3.6-M0：协议和行为合同冻结

- 冻结 `tool_calls` envelope、单调用结果、未知工具/重复 id/非法参数、最大调用数和 schema 版本。
- 冻结 capability 边界：`read/edit/write` 属于 FS，`bash` 属于 Shell；控制动作不进入 tool-call pool。
- 冻结结果顺序、abort、timeout、scheduler failure、policy/approval 和 checkpoint 语义。
- 冻结模型可观察的响应边界：一个响应只能是 `tool_calls` 或一个 `ask`/`skill`/`final` 控制 action，不能混合；tool calls 提交后，模型必须在下一 turn 决定控制 action。Prompt、parser、trajectory 和 A/B 指标都必须遵守该合同。
- provider 继续支持当前 JSON mode；原生 tool-call provider 只作为后续 adapter，不改变 scheduler 合同。

验收：协议单元测试、旧 Bash-only 回归和 `git diff --check` 全部通过；默认 profile 仍为 `baseline-bash`。

#### V3.6-M1a：FS `read` capability

- 只实现分页、有界、带行号和版本指纹的 `read`。
- 复用现有 workspace/policy/trace/checkpoint 适配层，不在本子阶段引入新的 CompletionVerifier 语义或并发行为。
- 本子阶段必须是独立提交，新增生产文件和能力域受 §5 停止规则约束；focused pytest 通过后才能进入 M1b。

验收：`read` 的路径边界、分页、截断、hash、trace/result 回放确定性测试通过；旧 V3.5 运行和 Bash policy 不下降。

#### V3.6-M1b：FS `edit` capability

- 只实现精确替换、显式 `replace_all`、必填 `expected_hash`、版本冲突拒绝和结构化 diff 的 `edit`。
- 缺失 `expected_hash` 必须结构化拒绝；不得用“同一 run 内先 read”替代版本检查。
- 独立提交并运行 focused pytest；不得同时修改 `write`、多工具调度或 Sandbox Runtime。

验收：唯一匹配、重复匹配、replace_all、hash 缺失/冲突、diff、越界和 checkpoint 不重复 edit 全部通过。

#### V3.6-M1c：FS `write` capability

- 只实现新建/完整重写、原子写入、hash/diff 摘要的 `write`。
- 新文件无需 `expected_hash`；已有文件必须提供并通过 `expected_hash`，缺失或冲突均结构化拒绝。
- 独立提交并运行 focused pytest；不得同时修改多工具调度、Context 或 Sandbox Runtime。

验收：新建、已有文件 hash 校验、原子写入失败清理、diff、越界和 checkpoint 不重复 write 全部通过。

#### V3.6-M2：多工具解析和 ordered result

- 一个模型响应允许多个 `tool_calls`，保持模型原始顺序。
- 调度器写入 `tool/call` 与 `tool/result` 成对事件；结果按模型顺序回填，即使 dispatch 完成顺序不同。
- 先以 `max_parallel_tool_calls=1` 上线，完成 provider/protocol、abort、超时和 scheduler failure 回归。

验收：多调用确定性测试通过；resume 不重复已提交的 edit/write/bash；baseline profile 行为不变。

#### V3.6-M3：只读并行和 exclusive barrier

- 连续 `read` 默认声明 parallel；`edit`、`write`、`bash` 默认 exclusive。
- 实现 DeepSeek Harness 风格 rolling pool 和 exclusive barrier；只有明确 parallel 才允许并行，异常分类 fail-closed。
- 记录 `max_parallel_tool_calls`，V3.6 初始默认 `4`，`1` 表示完全串行；该配置必须可覆盖，未来可用 A/B 评估是否提升到 DSH 的 `10`。
- abort 停止补充新调用并 drain 已启动调用；未启动调用写结构化 aborted result。

验收：并行确实发生、结果严格有序、文件内容无竞态、资源峰值在阈值内；V1.3/V2.0 checkpoint/resume 回归不下降。

#### V3.6-M4：证据链和回归

- 将 profile、并发上限、工具调用明细、policy/approval、耗时和 artifact locator 写入 state、trace、metrics、suite manifest 和报告。
- 只使用离线 trace/replay、合成 fixture 和确定性回归，准备 baseline/hybrid 所需的 schema、manifest、metrics、report 和证据定位；本阶段不调用 Provider，不消耗真实模型预算。
- 明确 baseline/hybrid A/B 的唯一真实运行归属为 M5；M4 产物不得被计入正式任务通过率或效率结论。
- 明确区分 task、agent、infrastructure、policy、protocol 和 tool runtime failure。

验收：离线 evidence replay、全量 pytest、`ruff`、`mypy`、package build、V3.5 固定回归和恢复回归不下降；报告无 dangling pointer、无覆盖旧 suite；Provider 调用数为 0。

#### V3.6-M5：真实模型 A/B 与默认值决策

- 在 M4 离线证据链通过后，冻结 source/case/verifier/provider/budget/sandbox/profile/并发上限，至少进行两轮独立 baseline/hybrid A/B；本阶段是 V3.6 唯一产生正式真实模型 A/B 的阶段。
- 报告任务通过率、turns、tool calls、Bash calls、只读并发、wall-clock、tokens、重复 I/O、失败/审批/timeout 和 diff 范围。
- 分别报告 tool-call step、control-action step 和总 turn；不能把 `tool_calls` 后下一 turn 的协议要求误算成工具能力差异。
- 只有 hybrid 通过率不下降、并发确实发生、审计和恢复完整且两轮结论同方向时，才考虑将 hybrid 设为默认。
- 若只减少 turns 或延迟而未提高任务成功率，只声明效率变化；任何能力提升必须有固定任务和报告路径支撑。

失败回退：回到上一个已验收 tag，保留 `baseline-bash`；不得同时修改 Context、Memory、Sandbox Runtime 和多工具调度来掩盖归因。

### V4.0：可验证多 Agent Harness 重构（experimental）

目标：在 V3.6 的 `read/edit/write/bash`、`ask/skill/final`、多工具调度、sandbox、policy、
checkpoint、trace、metrics、diff 和 verifier 之上，增加低成本模型可用的多 Agent 编排层，
将 miniCC 定位为：

```text
低成本模型上的可验证多 Agent Coding Harness
```

详细实施合同见本地不追踪文件
[`docs/V4_MULTI_AGENT_REFACTOR_PLAN.md`](docs/V4_MULTI_AGENT_REFACTOR_PLAN.md)。该文件不属于
当前 Stable 声明；路线图只冻结 V4 的设计、实施阶段和验收门，不把计划能力写成已实现能力。

#### V4.0 设计边界

V4 保留以下 profile：

```text
baseline-bash   V3.x 单 Agent 回退和历史对照
hybrid-v3.6     四工具 + 有界多工具调度的单 Agent 对照
multi-agent-v4  root + childrun + workflow + transcript，显式 opt-in
```

V4 必须支持：

- 单个 child Agent、多个 child Agent 并行和有界链式工作；
- 每个 child 独立 context、trajectory、metrics、checkpoint 和 trace namespace；
- miniCC 自己的 `childrun` 子进程，通过 stdin/stdout JSONL 通信；
- Provider、tool、child 和 transcript 的流式事件；
- child/workflow 的 interrupt、cancel、timeout、orphan 和 resume；
- `scout -> planner -> worker` 工作流；
- `worker -> reviewer -> worker` 有界复查回路；
- 同一 workflow 同时最多一个 workspace write lease holder。

V4 不允许多个 Agent 同时写入同一 workspace，不依赖 Prompt 文字实现只读安全，不调用外部
PI/Claude/Codex 进程作为自己的编排实现，也不展示或声称获取了模型隐藏 CoT。Transcript 中的
`Thought` 只能表示模型显式提供的短 `intent` 或 Harness 生成的有限 action summary。

#### V4.0 只读 child 安全合同

并行 `scout`、`planner`、`reviewer` 默认只允许 `read` 和受限 `bash`，且必须同时满足：

1. tool visibility 不暴露 `edit`、`write` 和 `delegate`；
2. runtime `CapabilityPolicy` 在 dispatch 前拒绝手写的未授权 tool call；
3. `ReadOnlyBashPolicy` 对已知重定向、写管道、删除/移动/复制、安装依赖和 Git 写操作保守拒绝；
4. 正式 sandbox 以 read-only mount 暴露 workspace，并在结束前后比较 workspace fingerprint。

只读 Bash 的字符串策略不是完整安全边界；`execute-local` 仅用于开发预检，不能作为只读安全
验收环境。正式只读 child 必须使用具备只读挂载能力的 sandbox，否则结果只能标记为不合格或
拒绝启动。`worker` 获取唯一 `WorkspaceWriteLease` 后才可使用 `edit/write` 和可能修改文件的
`bash`；lease 不能替代 path policy、approval、sandbox 或 expected hash 检查。

#### V4.0 Trace 与 Transcript 合同

V4 保留不可变的 `trace.jsonl` 作为机器账本，新增：

```text
transcript.jsonl   root turn/child span 的稳定可读投影
transcript.md      CLI/Web/面试演示的人类阅读版
```

一次 root 模型请求是一个 root `turn`。并行 child 是该 turn 内的 `delegate` step；每个 child
仍有独立的 child turns。child 完成后，只有结构化 `workflow_summary_observation` 进入 root
trajectory，child 完整细节通过 artifact/trace locator 查看。Transcript 至少展示：

```text
Thought/Intent -> Action -> Summary Observation
```

并明确标记 `model_intent` 与 `derived_summary`，不把隐藏推理当作可见 Thought。

#### V4.0-M0：合同冻结与残留清点

- 冻结 `delegate` schema、task dependency、role/profile、join、depth、预算、错误码和 output schema。
- 冻结 root/parent/child/workflow/span ID、childrun JSONL protocol、退出码和 cancellation 语义。
- 清点 `protocol.py`、`loop.py`、`state.py`、`checkpoint.py`、`cli.py`、eval runner、报告和旧文档中
  的单 Agent 适配；形成 profile compatibility matrix。
- 明确 `multi-agent-v4` 不改变旧 profile 默认值。

验收：协议/状态/schema 设计审查完成；旧 profile focused pytest、`ruff`、`mypy` 通过；没有将
未冻结字段先写入生产 state 或报告。

#### V4.0-M1：Runtime Seam 与权限边界

- 引入 `AgentRuntime`、`ChildCapabilities`、`CapabilityPolicy`、`ReadOnlyBashPolicy` 和
  `WorkspaceWriteLease`。
- 让 root、legacy single-agent 和 child 共用同一 tool/policy/sandbox/checkpoint pipeline，
  不复制第二套 executor。
- 增加 Docker read-only mount、workspace fingerprint 和 local executor 的明确降级语义。

验收：scout/reviewer/planner 无法执行 `edit/write/delegate`；未授权请求不进入 executor；只读
Docker child workspace 前后不变；两个 worker 不能同时取得 lease；V3.x 回归不下降。

#### V4.0-M2：ChildRun 生命周期

- 先实现 in-process child backend，用于确定性测试和开发；随后实现自己的 `minicc childrun`。
- child 拥有独立 ContextBuilder、trajectory、metrics、trace namespace、checkpoint 和预算。
- parent 只接收结构化 child result/evidence，不直接共享 trajectory。
- 支持启动、流式事件、完成、失败、timeout、interrupt、cancel、process crash 和 cleanup。

验收：单 child 3 次确定性运行结果结构一致；child 崩溃不会污染 parent state；子进程无残留；
JSONL partial output、EOF、invalid event 和 non-zero exit 都有结构化归因。

#### V4.0-M3：Delegate、并行与链式调度

- 增加 `delegate` action 和 `WorkflowCoordinator`。
- 支持单 child、只读 child 并行、依赖链、bounded `join=all/any` 和独立 child budget。
- root turn 将 delegate 视为一个嵌套 step；child 内部仍单独计 turn。
- workflow checkpoint 记录已提交 child result、未完成 task、process 状态和 lease epoch。

验收：两个 scout 确实并行且按声明顺序汇总；依赖 child 不早于前置结果启动；中断/恢复不重复
child 或已提交工具；并发上限、取消和未启动 task 的 aborted result 可从 trace 重放。

#### V4.0-M4：标准工作流与 Reviewer 回路

- 提供配置化 `scout -> planner -> worker` 模板。
- 提供 `worker -> reviewer -> worker` 模板，reviewer 只读并返回 finding/evidence，worker
  是唯一写入者。
- 复查次数、verifier 次数和 lease epoch 必须有上限和指标。

验收：固定 fixture 中 reviewer 可发现预置缺陷；worker 能根据 finding 修复；达到最大次数后
明确失败而不是无限循环；最终成功必须由独立 CompletionVerifier 判定。

#### V4.0-M5：Trace/Transcript、CLI 与 Viewer

- 新增 transcript projector、`transcript.jsonl`、`transcript.md` 和事件订阅总线。
- CLI 实时展示 root turn、intent、action、child start/progress/end、summary observation、
  tool summary 和 verifier；完整 stdout/prompt/secret 仍落 artifact 或脱敏。
- Web viewer 先消费 transcript，再按 locator 展开 trace/artifact；旧 trace 只读降级展示。

验收：一项真实代码任务可以只看 transcript 理解主链路；并行 child 显示为 root turn 内嵌阶段；
child 内部 turn 可展开；`model_intent`、`derived_summary`、tool arguments 和 artifact link 可验证。

#### V4.0-M6：旧适配与真实任务评测

- `baseline-bash`、`hybrid-v3.6` 保持原有入口和行为；旧 state/trace 按 legacy schema 只读读取。
- 提供 `LegacyBashAdapter`，不把 Bash 字符串猜测成 `read/edit/write`。
- 固定至少三类真实任务：失败测试修复、小功能+测试、陌生仓库/长日志调查。
- 比较 `hybrid-v3.6`、`multi-agent-v4` 和标准 workflow；冻结 provider、model、temperature、
  总 token budget、sandbox、case hash 和 repeat。

验收：报告同时包含 verifier verdict、workflow status、root/child turns、child 数、并发、耗时、
token、重复读取、policy/capability denial、write lease、diff 和失败归因；至少两轮独立 A/B；
旧 V3.x 固定回归与 resume 不下降。

#### V4.0-M7：发布门与声明边界

V4 从 experimental 进入稳定声明必须同时满足：

1. childrun 进程、流式、取消、恢复和清理均有确定性/集成证据；
2. read-only child 的 visibility、runtime policy、sandbox mount、fingerprint 四层合同均有测试；
3. 唯一 write lease 没有并发写入和未授权变更；
4. 单 child、并行 child、链式 child、独立 context 和 reviewer 回路均有回归覆盖；
5. transcript 能稳定呈现 `Thought/Intent -> Action -> Summary Observation`，但不声称隐藏 CoT；
6. 至少两轮真实固定任务对照有 root/child trace、transcript、metrics、state、diff 和 verifier；
7. 所有成功率、效率或质量数字都可以通过 run ID、artifact、报告和 verifier 复核。

在 M7 之前，简历和 README 只能声明“experimental 多 Agent 编排和可验证执行合同”，不能把
一次成功演示或计划中的 workflow 写成通用成功率。

### V4.1：Provider 层重构与多上游降级契约（已实现，确定性测试归档）

目标：把单一 OpenAI 兼容适配器（`OpenAICompatibleProvider` + 扁平 `provider:` 块）重构为多
route 注册表 + `LlmFailure` 归一化 + per-route `retryPolicy`（在失败步骤扩展点执行）+ 最外层
降级链，解决「硅基流动额度不够 / 换上游成本高 / 失败不可归类 / 重试写死且隐藏在 adapter 内」。
详细实施合同见本地不追踪文件
[`docs/V4_1_PROVIDER_REFACTOR_PLAN.md`](docs/V4_1_PROVIDER_REFACTOR_PLAN.md)。路线图只冻结
V4.1 的设计、实施阶段和验收门，不把计划能力写成已实现能力。

**完成状态（2026-08-22）**：V4.1 已完成实施并归档——M0 合同冻结、M1 `LlmFailure` 与
`complete()` 单次 attempt、M2 多 route 注册表与配置重构、M3 失败步骤扩展点重试、M4 最外层
降级链、M5 模型发现、M6 指标/文档/回归均已落地；下方 8 点验收门全部满足。全部能力由
`httpx.MockTransport` 确定性测试覆盖，不调真实 Provider；`acceptance/` 与既有 run/suite
证据零改动。以下 M0–M6 条目与验收门作为归档时的设计合同与验收记录保留。

#### V4.1 移植原则（对 deepseek-harness 的 `packages/llm` 设计）

- **设计决策照搬，字段名不照抄**：留下 `dsh-llm`/`dsh-llm-pi-ai`/`dsh-llm-retry` 已经过论证与
  测试的边界设计；字段名走 miniCC 自己的 snake_case，且只保留有消费方的子集。
- **黄金参数矩阵照抄**：唯一照抄数值的是经测试的重试配置（2 次重试 / 500ms 初始延迟 / 10s
  上限 / 10% 抖动 / 5 个暂时性 code），其出处为 dsh 笔记引用的 OpenCode / Pi / Codex。
- **破坏性重构，不做兼容别名**：移除 `Settings.provider/base_url/api_key/model/temperature`
  投影（不再保留为「默认 route 只读投影」），`cli.py` 约 15 处显示/指纹/指标填充显式改走
  route 名与 registry。

#### V4.1 设计边界

- `complete()` 是**一次可见 attempt**；重试不在 adapter 内部完成（隐藏的 SDK 重试会成倍放大
  预算，中间失败无法记进会话日志）。
- `LlmFailure` 是 provider 无关、可 JSON 序列化的失败事实 `{message, code, status,
  retry_after_ms, request_id}`，**不携带 `retryable`/`failover` 字段**——报事实的是适配器，
  定动作的是策略。
- `retryPolicy` 在 route 注册时**解析**、在 loop 的失败步骤扩展点（`loop.py:122` 的 `catch
  ProviderError`）**执行**；每次重试由一个已关闭的失败步骤 + 持久 `llm/retry` 事件表示，
  失败 attempt 不提交 assistant 消息、不虚增 turn 计数。
- **跨 Provider 的路由选择权在最外层调度器**：框架通过 `LlmFailure` 提供标准降级契约；
  `ProviderFailoverChain`（`core/failover.py`，可配置 `failover:` 块）消费该契约做跨 route 重路由，
  而 `core/provider.py` 与 `ProviderRegistry` 内部**没有任何** routing/failover、不偷换模型
  （一个 route = 一个 adapter 的不变量保留）。
- 稳定 code 集合保持很小（`RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT/EMPTY_RESPONSE` 为暂时性五码，
  `AUTH/QUOTA/BAD_REQUEST/CONTEXT_OVERFLOW/ABORTED/UNKNOWN` 非暂时）；`QUOTA` 单列以稳定区分
  「余额不足」与笼统 4xx。新增 code 需 fixture + 决策记录。

#### V4.1-M0：合同冻结

- 冻结 `LlmFailure` 稳定码清单与 HTTP/异常映射、`RetryPolicy` schema 与黄金默认、`providers`
  dict + `failover` + `child` 的 schema 与加载期 fail-fast 错误语义（未知字段、空 `base_url`、
  `default_provider`/`failover.chain` 指向不存在 route、顶层 `max_retries` 残留等一律点名报错）、
  `minicc models <route>` 输出契约。
- 验收：设计审查通过；不写未冻结生产字段；`ruff`/`mypy`/`pytest` 仍绿（尚无代码改动）。

#### V4.1-M1：`LlmFailure` 与 `complete()` 单次 attempt

- `provider.py` 引入 `LlmFailure`，`ProviderError` 改 `failure: LlmFailure`；删除 `complete()`
  内部重试循环，回归一次 `_post_json`/`_complete_stream`。
- HTTP 分支映射 401/403→`AUTH`、402/额度签名→`QUOTA`、429→`RATE_LIMIT`、5xx→`SERVER`、
  413/context 签名→`CONTEXT_OVERFLOW`；`Retry-After` 秒/日期/ms 解析 `retry_after_ms`、读请求 id；
  watchdog→`TIMEOUT`、`httpx.TransportError`→`TRANSPORT`、空正文→`EMPTY_RESPONSE`。
- `ModelResponse` 移除 `attempt_count`/`retry_reasons`。
- 验收：每码 ≥1 个 `httpx.MockTransport` 用例；`failure` 可 JSON 序列化且不含 `retryable`/
  `failover`；`complete()` 对可重试码也只发 1 次线路请求。

#### V4.1-M2：多 route 注册表与配置重构

- `config.py`：`ProviderRoute`/`RetryPolicyConfig`/`BackoffConfig` + `providers` dict +
  `default_provider`/`failover`/`child` 解析；移除两处扁平块与全部 `Settings.*` 投影；fail-fast。
- `provider.py`：`ProviderRegistry.build(route)`；`provider_name` 用 route key 优先。
- `cli.py`/`multi_agent.py` 两处构造点统一走 registry；约 15 处显示/指纹/指标填充改用 route 名。
- 验收：`test_config.py` 覆盖 dict 解析、非法 route、`api_key_env` 与 `.env` 注入、fail-fast 点名；
  主/子 route 可分别选上游；`ruff`/`mypy` 通过（投影已删，所有引用同步改）。

#### V4.1-M3：失败步骤扩展点的重试执行

- 新 `core/retry.py` 包裹 `loop.py:122`：读 `failure.code` 对 route `retry_policy`，有界退避 +
  jitter、尊重 `Retry-After`（超上限时 normal 放弃、always 回退本地退避）。
- 持久 `llm/retry` trace 事件（route、code、retry 序号、delay_ms、LlmFailure），wait 前落盘。
- `provider_request_attempts`/`provider_retried_requests` 由重试执行器累计（从 `_accumulate_usage`
  迁出）。
- 验收：可重试码触发、非可重试码不触发、退避在 jitter 边界内、normal 耗尽、always 无界、json_mode
  回退不计数、重试不虚增 turn/不提交 assistant 消息、注入 `random` 钩子确定性断言。

#### V4.1-M4：最外层降级链

- 新 `core/failover.py` 的 `ProviderFailoverChain`（`ModelProvider` 组合）消费 `LlmFailure` 契约；
  `failover:` 配置解析 + 校验（chain 引用存在的 route、非空、无重复、`max_hops >= 0`）。
- 组装点：存在 `failover` 时用链替换单一 route adapter，否则保持单一 adapter——降级链是可选最外层
  调度器，非默认姿势。
- 验收：`QUOTA/AUTH/SERVER/TIMEOUT/RATE_LIMIT` 耗尽触发切 route 且重发同 messages；`BAD_REQUEST/
  CONTEXT_OVERFLOW/ABORTED` 不切；每 hop 的 route 重试从 0 计数；registry/adapter 内无 routing
  分支（分层边界测试）。

#### V4.1-M5：模型发现

- 新 `core/discovery.py`：`GET {base_url}/models` 有界读取（4 MiB），解析 `data[].id` 与可选
  context/max token；`401/403→AUTH`、非 2xx/非 JSON/无 `data` 数组给结构化失败。
- 新 CLI `minicc models <route>` + `--probe-key`（临时试密钥不落地）。
- 验收：`httpx.MockTransport` 覆盖正常列表、401、畸形 JSON、超大 body、无 `data`、带路径 base
  的拼接；对目录外中转站能列模型。

#### V4.1-M6：指标 / 文档 / 回归

- `provider_name`→route key，补 `provider_failure_code`、`llm/retry`、failover hop 事件落点；
  更新 `CLAUDE.md` 与 `minicc.yaml` 样例（硅基流动 + 百炼 + 中转站 + failover 示例），移除
  `MINICC_FAST_MODEL`。
- 验收：`test_provider.py` 用 `httpx.MockTransport` 重写后全绿；`minicc --help` 出现 `models`；
  不触碰 `acceptance/`；`ruff`/`mypy`/`pytest --cov`/`uv build`/`git diff --check` 通过。

#### V4.1 验收门与声明边界

进入 Stable 声明必须同时满足：

1. `providers:` 多 route + `default_provider` + `child.provider` 可用，主/子 Agent 可分别选上游，
   换 provider = 改一行配置（`MINICC_PROVIDER` 或 `default_provider`）。
2. 每个 provider 失败携带稳定 `LlmFailure`（无 `retryable`/`failover` 字段），`QUOTA` 区分
   `AUTH`/4xx。
3. `complete()` 单次 attempt；每 route 重试独立可配、在失败步骤扩展点执行，退避含 jitter、尊重
   `Retry-After`、持久 `llm/retry` 事件。
4. 最外层降级链消费 `LlmFailure` 契约做跨 route 重路由，adapter/registry 内无 routing/failover。
5. `minicc models <route>` 对目录外中转站可列模型。
6. `ruff`/`mypy`/全量 pytest coverage/`uv build`/`git diff --check` 通过，覆盖率不降
   （`fail_under = 50`，补测试达成，不缩小统计范围）。
7. 确定性验证全程用 `httpx.MockTransport`，不调 Provider；真实 smoke 仅可选、gitignored、需密钥，
   不进 acceptance。
8. `acceptance/` 与既有 run/suite 原始证据零改动；不覆盖任何 suite/report。

以上 8 点验收门已全部满足（2026-08-22），V4.1 的 provider 层多上游、降级、发现能力已作为
确定性测试覆盖的稳定实现归档；真实模型 smoke 仍仅可选、gitignored、需密钥，不进 acceptance。
V4.1 与 V4.0 的多 Agent 编排正交（child 模型同走该 provider 层），不夹带未验收的 multi-agent
变更一起归因。归档证据：三个提交 + 末尾 `V4.1` tag，全程确定性验证、未触碰 `acceptance/`。

### V4.2：Benchmark 执行链收敛（回合上限 + 收尾自述）

目标：承接 V4.1 实测暴露的缺口，把 benchmark 执行链收敛为「单 Agent → 模型自述 `final` →
post-hoc 打分」，不引入 verifier 回喂闭环。方向经三方对照（dsh / pi）确定：**跟随 dsh 的
prompt 级自述 + 权威性检查，信任模型 `final`，不跑隐藏测试、不回喂失败**——对小模型而言
worker→verifier→worker 闭环太重、易死循环。详细设计见本地不追踪文件
[`docs/V4_2_BENCHMARK_EXECUTION_LOOP_PLAN.md`](docs/V4_2_BENCHMARK_EXECUTION_LOOP_PLAN.md)。

**状态（2026-08-22）**：已实现并全量回归通过。Block A（`max_turns`，`core/loop.py:55/152-153`、
`config.py:102/228`、`cli.py:1304/2160`）+ Block B（收尾 grounding，`prompts/agent.py:23-26`）
均落地；`ruff check src tests` / `mypy src/minicc` 通过，`pytest` 463 passed，`acceptance/` 与既有
run/suite 零改动。唯一连带修正：`tests/test_context.py` 的 epoch 压缩测试预算 3000→4000，消除其与
稳定前缀长度的脆弱耦合（grounding 使 `STABLE_PREFIX` 增长 ~232 字符）。真实模型 smoke 仍可选、
gitignored、需密钥、不进 acceptance。

设计边界：

- **不新增** in-loop 验证 / 回喂 / `python_verifier` 断言 / `verification_attempts` / `max_verification_rounds`。
- **`CompletionVerifier` 保持 opt-in**（`completion_gate` 仍显式开启才挂载），不进 benchmark 默认。
- **成功 = 模型 `final`**：harness 信任自述（对齐 dsh `complete`、pi「模型停止即完成」），post-hoc
  `run_assertions` 是唯一、不可变的通过/失败打分器。
- **不修不删** `reviewer_loop` 死骨架（`multi_agent.py`），也不接线 benchmark。
- 只补一个 prompt 收尾约束，不新增任何 action/机制。

#### V4.2-M1：回合上限（成本早停）

- `LoopConfig`/`BudgetSettings` 新增 `max_turns`（0 = 不限，默认 0）；`AgentLoop.run` 在现有
  `max_seconds` 检查处加 `max_turns` 达上限即 `failed` 早停。
- 不做 token 上限：`max_turns` 已覆盖成本约束（回合数 × 有界单回合成本，dsh 也只有 `maxRounds`）。
- `case.yaml` 支持 `budget.max_turns`，失败烧 token 的单个 case（如 `scale-generator`）可声明自己的 cap。

#### V4.2-M2：收尾自述 grounding

- `prompts/agent.py::STABLE_PREFIX` 的 `final` 规则旁补一条 grounding：`final` 的 `answer` 只写本次
  bash/observation 实际证实的、说清关键结论由哪条命令验证、不编造会话里没有的结果——对应 dsh
  `wrapup.ts` 的 GROUNDING。

验收门（确定性，不调 Provider）：

1. `ruff check src tests`、`mypy src/minicc`、全量 `pytest`、`git diff --check` 通过，覆盖率不降
   （`fail_under = 50`，补测试达成，不缩小统计范围）。
2. 新增确定性测试：`max_turns` 达上限 → `failed` + summary 含 `max_turns`；默认 0 不限；
   `case.budget.max_turns` 覆盖生效；`STABLE_PREFIX` 含 grounding 子串。
3. `acceptance/` 与既有 run/suite 原始证据零改动；不覆盖任何 suite/report。
4. 真实模型 smoke 仅可选、gitignored、需密钥，不进 acceptance。

### V5.0：会话式改造（Session + Chat + Web，experimental）

方案文档：[`docs/V5_0_SESSION_CHAT_REMODEL_PLAN.md`](docs/V5_0_SESSION_CHAT_REMODEL_PLAN.md)，
记忆线依赖 [`docs/V5_1_MEMORY_REDESIGN_PLAN.md`](docs/V5_1_MEMORY_REDESIGN_PLAN.md)。

目标：在 run/eval 之上叠一层 **会话（Session）**，把「一次性 goal→run」改成「多轮对话→每轮一个
run_id，仍产出 trace/metrics」。整体五层模型为 Project → Session → Turn → Run → Message；
`transcript.jsonl` 是唯一事实源（append-only JSONL，`seq` 单调，`role:user/assistant`），
`session.json` 只存元数据。**安全 = 双模式分工**：会话走「真实工作目录直跑 + 审批链 + git 回滚」，
run/eval 保留既有隔离拷贝（快照复制 + `diff.patch`）作为块状模式专用，二者解耦执行隔离与
workspace 生命周期。

按 P0→P1→P2→P3 落地：

- **P0 会话骨架 + 多轮 loop**：`core/session_store.py`（`.minicc/sessions/<id>/{session.json,transcript.jsonl,runs/<run_id>/}`，
  session_id `YYYYMMDD-HHMMSS-<8hex>`）、`core/session_engine.py`（可重入 turn loop，注入
  `loop_factory` / `on_approval` / `on_turn_end`）、`session` 命令族、`build_messages` 改读 transcript
  （**向后兼容**：无 transcript 时退回单 goal，eval 不红）。
- **P1 聊天工作区 + 安全适配**：真实目录直跑、审批链继续 run 内 cycle、run/eval 隔离拷贝仍保留。
- **P2 Web chat server + steer/append**：`server/chat.py`（纯标准库 `ThreadingHTTPServer` + SSE +
  单页前端），turn 通过 `submit_turn` / `resolve_turn` 纯函数驱动，steer 为 best-effort 追加 redirect。
- **P3 记忆挂接**：只落 `TurnEndHook` seam（`memory_turn_end_hook_errors` 指标），L1 蒸馏留待 V5.1。

当前状态（2026-08-23）：**已实现（experimental，尚未真实模型验收）**。P0–P3 全部落地并通过确定性
测试（`ScriptedProvider` / `RecordingExecutor` / `PolicyChain([ApprovalPolicy])` 驱动，不调 Provider），
全量 `pytest` 509 passed，`ruff` / `mypy` 通过。按 CLAUDE.md 约定：`experimental` 能力只有在确定性测试
+ `acceptance/` 真实模型验收归档后才能升 `stable`，故本节只宣称「已实现」、暂不夸大。

### V5.1：记忆子系统改造（L0→L3 金字塔 + 检索注入，experimental）

方案文档：[`docs/V5_1_MEMORY_REDESIGN_PLAN.md`](docs/V5_1_MEMORY_REDESIGN_PLAN.md)。

目标：把「三个互不相干、靠哈希仪式撑着的记忆半成品」（feedback 规则本 / working memory / compaction）
换成有明确语义的四层记忆金字塔 —— **L0** 会话 transcript + turn trace → **L1** 原子记忆 → **L2**
场景知识块 → **L3** 核心画像。回合末用一次 LLM 调用把 L1 提炼进 SQLite（FTS5 检索）；按阈值把 L1
升维成 L2/L3（persona + scenario）；回合开始双轨注入（L2/L3 进 system 缓存轨、L1 进每轮
`<relevant-memories>` 块），全程优雅降级、失败不阻断。丢弃 working-memory 的四重 SHA 证据仪式。

分阶段 P0→P4 落地：

- **P0 L1 提炼 + SQLite/FTS5 + 降级**：新 `memory/l1.py`（`L1Distiller`/`MemoryStore`，每项目一个
  `.minicc/memory/<project-hash>.db`，四表 `memories`/`memories_fts`/`scenarios`/`persona`）、
  `MemoryTurnHook` 挂在 turn 末、`<relevant-memories>` 注入 + 预算、提炼/召回/入库全程降级。
- **P1 L3 persona 升维 + 双轨注入**：`memory/escalation.py` 的 `PersonaEscalator`（project 级
  `preference`/`constraint` 确认 ≥3 或显式强调词「以后都/记得/规则是/总是/从来不/记住」触发一次
  LLM 合成），手写 JSONL 与自动 L3 合并 view（手写优先）；L3 进 system 缓存轨（stable prefix 尾部）。
- **P2 L2 scenario 升维**：`ScenarioEscalator` 按 `source.file` 主题聚类 ≥5 条 L1 触发
  `{scenario,summary,recipe}` 总结，并入 system 缓存轨。
- **P3 LLM dedup + 可选 embedding**：`memory/dedup.py` 的 `L1Deduper`（store/skip/update/merge
  四动作）；可选 `Embedder = Callable[[str], list[float]]` 走 RRF 融合，无则退回纯 BM25。
- **P4 评测重写 + 跨会话验收 + 删哈希仪式**：`memory/working.py` 删掉 file/excerpt/payload/project
  digest 四重哈希与 `WorkingMemoryError`-abort，改为失败跳过（记 `working_memory_invalid_adoptions`
  指标、**绝不 raise**）；`scope:project` 跨会话连续性（新 session 无需重读即召回旧记忆）用确定性
  测试落位。

当前状态（2026-08-23）：**已实现（experimental，尚未真实模型验收）**。P0–P4 全部落地并通过确定性测试
（`ScriptedProvider` / fake provider 驱动，不调真实 Provider），新增 `tests/test_l1_memory.py`、
`test_escalation.py`、`test_scenario.py`、`test_dedup.py`、`test_embedding.py` 与一份 `scope:project`
跨会话验收用例；`ruff` / `mypy` / 全量 `pytest` 573 passed，`acceptance/` 与既有 run/suite 零改动
（trace/ledger 的 SHA 锚定是另一子系统，未动）。按 CLAUDE.md 约定，`experimental` 只有在确定性测试
+ `acceptance/` 真实模型验收归档后才能升 `stable`，故本节只宣称「已实现」、暂不夸大。

### Sandbox Runtime 生命周期治理（当前小步迭代）

目标：在不引入 Compose、容器池、标签清理平台或多运行时的前提下，把现有“一次
Agent run 对应一个临时 Docker 容器”的生命周期做成可解释、可回滚、可测试的最小闭环。

当前范围：

- 容器启动失败时按已知 run 名称立即回滚，避免 `docker run` 部分成功后遗留 `Created` 容器。
- `docker exec` 超时后销毁整个容器，将 run 标记为失败并保留宿主机 workspace/artifacts。
- 增加可跳过的真实 Docker 集成测试，覆盖启动、执行、只读/可写挂载、超时销毁和失败启动回滚。
- 在首次容器启动前检查 Docker CLI 和 daemon；镜像解析与拉取仍由 `docker run` 负责。

明确不纳入：容器标签和清理子命令、预热池、sidecar、execd、Firecracker/gVisor、Compose
编排、远程控制平面和多租户调度。多个并发 run 继续依靠现有唯一 `run_id` 隔离。

完成条件：focused 单元测试通过；Docker 可用的 CI 环境中真实集成测试通过；启动失败和命令
超时均不留下由本次 run 创建的容器；Docker 不可用时在模型调用前返回明确错误。

当前状态（2026-08-20）：实现与单元回归已完成（353 passed）；本机 Docker daemon 不可用，
真实集成测试暂未执行，待 Docker Desktop 恢复后补跑。

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
                                      |                             +-> V3.0 -> V3.1 -> V3.1.1 -> V3.2
                                      |                                                        |
                                      |                                                        +-> V3.3 real-repo demo -> V3.4 minimal real-project eval -> V3.5 public benchmark and controlled experiments -> V3.6 hybrid FS/Shell tools + bounded multi-tool scheduling -> V4.0 experimental multi-agent harness -> V4.1 provider refactor with multi-upstream degradation contract (已归档) -> V4.2 benchmark execution convergence (回合上限 + 收尾自述，已实现) -> V5.0 会话式改造 (Session + Chat + Web，experimental) -> V5.1 记忆子系统改造 (L0→L3 金字塔 + 检索注入，experimental)
                                      |                             |
                                      |                             +-> V2.1 compaction
                                      |                                    |
                                      |                                    +-> V2.1.1 prompt cache
                                      |                                               |
                                      |                                               +-> V2.1.2 cache utilization
                                      |                                                          |
                                      |                                                          +-> V2.2 memory
                                      |
                                      +-> experimental/runtime-tools
                                      +-> experimental/meta-review
```

V2.1 与 V2.2 是增强线，其中 V2.1 已完成验收；V2.2 不应阻塞 V3.0 的 Harness 发布。V3.0 仍必须通过 ETCLOVG 能力证据矩阵完整披露七层状态；“不阻塞发布”只表示允许标记为 `experimental`，不表示可以省略或宣称已经稳定。

## 7. 最终可声明的项目效果

Stable V3.0 最终不是追求和他人简历逐字一致，而是形成同等强度的证据链：

```text
实现 Coding Agent Harness 的模型接入、action/tool 治理、workspace 隔离、
PolicyChain、trace、metrics、diff 和 report 执行闭环；在固定回归任务上达到
可复现的通过率和预算内完成率；通过受控中断场景验证 checkpoint/resume 的
状态保真，并用 A/B 实验量化上下文压缩和分层记忆对 prompt 与重复 I/O 的影响。
```

具体百分比和优化数字必须由最终报告生成，不能提前写入项目说明或简历。

V3.5 通过后，才可以在 Stable V3.0 的工程能力声明之后增加作用域明确的公开基准和受控实验声明：
固定题目数量、repeat、Provider、source commit、Verifier 版本和报告路径必须同时写出。V3.5 未通过时，
只能继续使用此前已经通过验收的证据，不得把 calibration、单次演示或计划中的指标写成正式 benchmark
结果。

若 V3.3 正式 A/B 通过，可在上述工程闭环之外增加一条有严格作用域的 Capability Lift 声明；若未
通过，则继续只声明稳定的工程能力、固定回归和效率实验，不得把“具备 Gate/Onboarding”改写成
“已经提高真实任务成功率”。
