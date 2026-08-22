# miniCC

miniCC 是一个面向面试展示的 Bash-first CodeAct Agent Harness。

它的目标不是复刻完整 Claude Code，也不是堆很多工具，而是把 Coding Agent 背后的工程层拆清楚：模型负责推理和生成 action，harness 负责协议校验、Provider 适配、执行编排、状态管理、策略、安全、上下文、trace 和 eval。

## 项目定位

一句话概括：

```text
miniCC 用极简 bash / ask / final action space 承载模型智能，用 harness 把执行过程变得可控、可观测、可回归。
```

## 10 分钟证据演示

在依赖和 Docker 镜像已就绪的机器上，以下路径不调用 Provider，约 5 分钟即可验证发布证据闭环：

```powershell
uv sync
uv run pytest -q tests/test_release_report.py tests/test_server.py
uv run minicc release-report
uv run minicc web --host 127.0.0.1 --port 8000
```

`release-report` 会生成系统回归、Context、Memory、Resume 四维 JSON/Markdown/CSV 和 manifest，
每个数字都带 run ID、配置、原始 artifact 与复跑命令。需要真实运行时，先在 `.env` 里配置
`MINICC_API_KEY` 再执行单 case；七层能力状态与边界见
[`miniClaudeCode.md`](miniClaudeCode.md)（ETCLOVG 面试讲稿）。

当前项目按 6 个基础里程碑推进，随后按版本里程碑（V2.x → V3.x → V4）演进，完整路线图见
`STABLE_V1_MILESTONE_ROADMAP.md`：

```text
M1: uv 项目骨架、Provider Adapter、Action Protocol、Minimal Agent Loop
M2: workspace copy、Docker sandbox、Observation contract、Artifact store
M3: PolicyChain、Command/Network/Budget policy、ask/approval/resume
M4: Prompt builder、prompt cache 友好布局、context budget、semantic compaction
M5: Goal-relevant Skill/Feedback selection、Trace events、Metrics
M6: Eval runner、Web trace viewer、文档与面试示例
```

## 当前版本

当前发布版本为 `3.7.0.dev0`（`minicc.yaml` 的 `project.milestone` 为 `v4.1`），默认 tooling profile 是
`baseline-bash`。项目在 Bash-first 极简 action space 之上按 profile 演进出三套工具面，由
`tooling.profile` 选择：

```text
baseline-bash   默认；只有 bash / ask / final / skill，走 BashExecutor（历史对照与可回退）
hybrid-v3.6     FS 工具 read / edit / write + bash，支持有界多工具调用 tool_calls
multi-agent-v4  delegate 多 Agent 编排（experimental），root + childrun + workflow + transcript
```

V3.6 已把结构化 hybrid 工具面与有界多工具调度归档为确定性实现；V4 多 Agent Harness 已实现但
仍标 experimental（见下文）。以下 V2.x/V3.x 逐段是历史验收叙事，保留为“简历数字→原始证据”的
关联链，结论仍只属于对应归档版本。

Stable V3.2 将目标相关 Skill catalog 与 commit-bound Feedback rules 的
确定性选择升格为 stable。Stable V3.1.1 的 CI、覆盖率、lint、类型检查、构建检查和发布治理继续
生效。底层 Harness
继续继承 Stable V3.0：固定 C01/C02/C03/C04/C09 系统矩阵在执行提交
`7d346fb77a191f0a5dbbb3157419cd0c0079c0cf` 上达到 15/15 PASS；正式聚合器在验证提交
`cc150b0ae815e2add2f4ac036b3e0371205ddda4` 上逐 run 复核资格，二者之间仅包含报告验证器及其
测试。最终四维报告覆盖系统回归 15 runs、Context 24 runs、Memory 27 runs、Resume 1 run，
所有 claim 均携带配置、run ID、原始 artifact 和复跑命令。归档见 `acceptance/stable-v3.0/`。

上下文与记忆采用分层设计：运行态 trajectory/state summary 只服务当前 run；显式
working memory 通过 `final.memory` 声明文件行区间，并在 follow-up 时校验文件、哈希和项目快照；
Feedback Memory 只提供项目级 `never`/`prefer`/`caution` 规则选择。

Stable V3.1 新增显式触发的离线
Meta Review：`minicc meta-review <run_id>` 读取已结束 run 的不可变证据，在独立
`.minicc/meta-reviews/` 目录生成带来源哈希和模型调用指标的审查结果，不修改源 run，也不自动
采纳建议。C02 A0/A1 分别为 3/3 与 3/3 PASS；A1 三个 run 均产生 schema-v2 真实模型审查，
合计 11 条 finding、11 条关联建议和 21 个可重新解析的证据引用，20 项聚合门全部通过。
四文件归档见 `acceptance/stable-v3.1/`。Stable 声明限于审查链路、证据真实性、建议可追踪性和
固定 case 非回归；尚未声明实际采纳建议能提高后续任务质量。

Stable V3.2 在执行提交 `178d3ed142b1d492c741539685cb13b51aa075f0` 上完成固定 G01 的
A0/A1 各 3 次真实模型验收，两侧均为 3/3 PASS 且无 Provider/protocol failure。A0 不选择任何
指引；A1 三次均且只选择 `release-manifest` 与 `release-legacy-id`。直接注入带哈希的相关 skill
正文后，Bash 动作总数从 13 降至 6（-53.85%），prompt tokens 从 16,683 降至 8,162
（-51.08%）。完整四文件归档见 `acceptance/stable-v3.2/`。该结论只覆盖固定 case，不声明自动
经验提取、环境式检索、RAG 或跨任务质量提升。

当前开发版在这份历史验收之上改为懒加载：稳定 prompt 只放目标相关 skill 的名称、描述、来源
和 catalog digest；模型通过 `skill` action 才取得冻结版本的正文。Registry 按
`skills/`、`.agents/skills/`、用户目录和 bundled 目录的顺序合并，同名 skill 以前者覆盖，
frontmatter 使用 PyYAML 校验。该改动已有确定性测试，但尚未重跑 V3.2 真实模型 A/B。

V3.3 当前处于 `experimental` 开发阶段：已增加只读 Repository Inspector、受限
`MINICC.md` Project Guide 注入和可选 Runtime Completion Verification Gate。模型请求 `final`
时，显式启用的 Gate 会运行预绑定 Verifier，失败结果回到同一 Agent Loop，只有通过后才允许
`completed`。这两项能力已有确定性测试，但尚未完成真实 Spring Boot 仓库的 3-run 验收，因此不属于
Stable V3.2 声明，也不宣称已经提高真实仓库成功率。

V3.4 增加了最小真实项目评测闭环，任务位于
`eval_cases/real_project_suite_v1/`。它从 `MyHeiMaDianPing` 的真实业务场景抽取最小 Java fixture，
不复制完整仓库；每次运行先要求初始 `verify` 失败，再由单 Agent 修改临时 workspace，最后由运行器
独立执行最终 `verify`，保存初始/最终验证 artifact、diff、trace 和 `verdict`，并自动清理 workspace。
任务自报完成不算通过。确定性验证可直接运行：

```powershell
python eval_cases/real_project_suite_v1/R01_cache_delete_retry_boundary/fixture/verify.py
python eval_cases/real_project_suite_v1/R02_retry_policy_regression_test/fixture/verify.py
python eval_cases/real_project_suite_v1/R03_cache_key_builder_feature/fixture/verify.py
```

真实模型评测使用 `uv run minicc eval eval_cases/real_project_suite_v1 --repeat 3 --execute-local`；
Provider、Java toolchain 或清理问题会记为 `infrastructure_error`，不混入任务通过率。当前阶段仍不
引入 GitHub 拉取、完整仓库复制、worktree、并发或 patch replay。

V3.6 在保留 `baseline-bash` 回退能力的前提下新增结构化 hybrid 工具面
（`tooling.profile = hybrid-v3.6`）：模型通过 `tool_calls` 一次发起至多 `max_tool_calls_per_step`
个调用，`read` 可有界并行（`max_parallel_tool_calls`，默认 4），`edit`/`write`/`bash` 是排他屏障；
`read`/`edit`/`write` 只面向 workspace 内相对路径，已有文件的 `edit`/`write` 必须携带与最近一次
读取一致的 `expected_hash`，版本冲突以结构化错误拒绝，不静默降级。确定性证据
`acceptance/stable-v3.6/` 由 `tools/run_v36_offline_evidence.py` 生成且 `provider_calls: 0`；正式
M5 真实模型 A/B 尚未开展，因此不声明 hybrid 提高任务成功率，默认 profile 仍为 `baseline-bash`。

V4 在 hybrid 工具面之上增加 `delegate` action 与 `WorkflowCoordinator`
（`tooling.profile = multi-agent-v4`），将 miniCC 定位为“低成本模型上的可验证多 Agent Coding
Harness”。角色 `scout`/`planner`/`reviewer` 只读、`worker` 持有唯一 `WorkspaceWriteLease`；
`CapabilityPolicy` / `ReadOnlyBashPolicy` 提供运行时权限边界，正式只读 child 还要求 Docker
read-only mount 与 workspace fingerprint 四层合同。child 通过 `minicc childrun` 子进程的
stdin/stdout JSONL 运行，子模型由 `child` route 指定；不可变 `trace.jsonl` 投影为脱敏
`transcript.jsonl`/`transcript.md`。确定性实现证据在 `acceptance/experimental-v4/`
（370 passed、3 skipped），但仍是 experimental：未做真实 Provider 多 Agent A/B，不声明多 Agent
通用成功率；旧 profile `baseline-bash` / `hybrid-v3.6` 默认行为不变。

V4.1 把单一 Provider 适配器（扁平 `provider:` 块）重构为多上游降级契约：`minicc.yaml` 用
`providers:` dict 枚举上游 route（硅基流动 / 百炼 / 私人中转站，每条 route 独立 `base_url`/
`api_key_env`/`model`/`retry_policy`），配 `default_provider` + 可选 `failover` 最外层降级链 +
`child` 子模型 route，换 provider = 改一行配置（`MINICC_PROVIDER` 或 `default_provider`）。每个
provider 失败归一成稳定 `LlmFailure`（`RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT/EMPTY_RESPONSE` 与
`AUTH/QUOTA/BAD_REQUEST/CONTEXT_OVERFLOW/ABORTED/UNKNOWN`，载荷不含 retryable/failover 字段）；
`complete()` 回归单次可见 attempt，重试上移到 loop 失败步骤扩展点（per-route、有界退避 + 抖动 +
尊重 `Retry-After`、落 `llm/retry` 事件），跨 route 重路由交给最外层 `ProviderFailoverChain`
（落 `failover/hop` 事件），adapter/registry 内无任何 routing 分支。新增 `minicc models <route>`
对目录外中转站做有界 `GET /models` 发现。全部能力由 `httpx.MockTransport` 确定性测试覆盖，
不调真实 Provider；真实模型 smoke 仅作为可选、gitignored、需密钥的开发预检，不进 acceptance。

Stable V2.0 已完成 10 个 checkpoint/resume 状态场景、3 个执行式中断场景和 1 个真实模型恢复 run，恢复后的 workspace、trajectory、diff 与终态一致，已完成 action 重复执行次数为 0；同时 V1.3 的 C01-C04/C09 完整矩阵继续保持 15/15 PASS。完整证据见 `acceptance/stable-v2.0/`，V1.3 原始验收仍保留在 `acceptance/stable-v1.3/`。

Stable V2.0.1 已修复 workspace 可见文件、Git baseline 和最终 diff 的证据不一致：Git 项目从固定
commit 建立快照，dirty/untracked 状态显式固化，ignored 文件默认排除，敏感目录受硬性 deny，
每个 run 生成 `workspace_manifest.json`。C02 正式 release gate 为 3/3 PASS，完整回归为 132/132
PASS，证据见 `acceptance/stable-v2.0.1/`。

Stable V2.0.2 已把 run、suite、version index 和 report 拆成 schema v2 技术账本：每次 eval 使用
唯一 suite/run ID，报告不可覆盖，正式指标只接纳证据完整且语义明确的记录，Viewer 与 cleanup
能够处理 legacy、orphaned 和缺失可选 artifact。最终提交上的 C02 账本验收为 3/3 PASS，V1.3
五案例回归为 15/15 PASS，18 个正式 run 的证据与指标资格均为 18/18；完整归档见
`acceptance/stable-v2.0.2/`。

Stable V2.1 已完成两轮独立 Context Compaction A/B：A0/A1 首轮均为
3/3 PASS，第二轮均为 9/9 PASS；A1 的平均 prompt 长度相对 A0 分别下降 9.27% 和 46.60%，
关键事实保留率均为 100%，重复 I/O 满足验收容差。完整归档见
`acceptance/stable-v2.1/`。Skill/Feedback Memory 仍保持 experimental。

Stable V2.1.1 在固定序列与真实 C02 上完成两轮独立、倒序
Prompt Cache A/B；P0/P1 真实任务均为 3/3 PASS 且没有 Provider 重试。P1 的真实命中率由
3.32%/3.40% 提高到 23.20%/24.45%，未缓存 token 分别下降 31.75%/34.77%，总 prompt
分别下降 14.08%/16.60%。默认消息布局已切换为 `append`，完整归档见
`acceptance/stable-v2.1.1/`。

Stable V2.1.2 在实现提交
`de3898ed54431f45cca9c83535bee2a5c5529b4e` 上完成 `formal-v212-round-81`（P1-first）与
`formal-v212-round-82`（P2-first）两轮正式验收。P2 固定长序列 full-chain 命中率为
87.65%/84.82%，steady-state 为 94.00%/91.53%；真实 C07 full-chain 为 75.89%/74.73%，
steady-state 为 83.09%/81.84%，12 个 C07 run 全部保持精确 9 请求、8 Bash 动作链与 100%
任务通过率。最终归档仅含四个文件，8 份入选输入自包含于 `evidence.json`，见
`acceptance/stable-v2.1.2/`。

Stable V2.2 在共同执行提交
`15fadae08d7d424853ba24b4dca534501493a183` 上完成 M01/M02/M03 三组正式 source/M0/M1
配对评测，共 27 个 run 全部 PASS。M0/M1 follow-up 关键事实正确率均为 9/9，重复来源文件读取
由 `9` 降为 `0`，follow-up prompt token 由 `36878` 降为 `26617`（下降 27.82%）；旧 run
串入、无关注入、完整性无效采纳、Provider error/retry、protocol error 和审批均为 0。验收读取器
修复提交为 `ba5ac0cdb5003dc9a029943f5469820f6a31a5e0`，复用了未受影响的正式 run。最终归档只含
四个文件，见 `acceptance/stable-v2.2/`。

M1 已实现基础闭环：

- 使用 `uv` 管理 Python 项目。
- 提供 `minicc` CLI 入口。
- 实现 OpenAI-compatible Provider Adapter。
- 归一化模型 usage 和 prompt cache 指标。
- 实现严格 JSON action schema，只允许 `bash`、`ask`、`final`，并兼容常见 Markdown/`<function>` JSON 外壳。
- 实现最小 Agent Loop：构建 prompt、调用模型、解析 action、处理 bash/ask/final。
- 提供可注入 executor，方便后续替换为 Docker sandbox。
- 补充单元测试覆盖 Provider、Protocol 和 Loop。

M2/V2.0.1 已实现执行与 workspace 证据治理层：

- Git 项目从固定 source commit 创建独立快照；源目录的 tracked dirty patch 和允许的 untracked 文件会显式固化为 run baseline。
- 非 Git 项目使用受控 copy fallback；嵌套的 eval fixture 不会借用父仓库作为 Git 根。
- `.workbuddy`、`.minicc`、`.env`、虚拟环境、缓存和构建目录受硬性 deny 保护；Git ignored 文件默认不进入 workspace。
- 必需的 ignored 文件只能通过 `workspace.ignored_allowlist` 显式声明，硬性 deny 始终优先。
- 每个 run 会生成 `workspace_manifest.json`，任务结束后相对固定 baseline 生成 `artifacts/diff.patch`。
- 默认使用 Docker sandbox 执行 bash action。
- Docker 容器默认禁网，并限制 CPU、内存、PID、capabilities 和 no-new-privileges。
- 命令结果会标准化为 `Observation`：`command_result`、`no_output`、`command_error`、`timeout` 等。
- 超长 stdout/stderr 会写入 artifact，prompt 中只保留 preview、artifact id 和路径。

M3 已实现策略中间件和 HITL 基础链路：

- 实现 `PolicyChain`，bash action 在进入 executor 前必须先经过策略链。
- 实现 `CommandPolicy`，拦截 `sudo`、危险 `rm -rf /`、`shutdown`、`mkfs`、`mount` 等高危命令。
- 实现 `PathPolicy`，拦截 `/mnt`、`/var/run/docker.sock`、`/root/.ssh` 等敏感路径。
- 实现 `NetworkPolicy`，locked mode 下对实际执行的 `curl`、`wget`、`git clone`、`pip install`、`npm install` 等联网动作要求审批或拒绝；here-doc 文档正文中的同名文字不误判为命令。
- 实现 `BudgetPolicy`，限制 bash action 次数，并把超长 timeout 改写到配置上限。
- 实现 `ApprovalPolicy`，对删除类高风险动作触发人工审批。
- `ask` 和 `require_approval` 会让 run 进入 `waiting_approval`，并把状态保存到 `state.json`。
- 新增 `approve`、`deny`、`resume` CLI 命令，支持 Stop and Resume 风格的审批恢复。

M4 已实现稳定的上下文构建基础链路：

- 新增 `ContextBuilder`，统一承载 prompt assembly、context budget 和 compression 逻辑。
- Prompt 按 Stable Prefix / Dynamic Context 分层组装，把 action 协议、policy 摘要和 observation contract 固定前置。
- 实际项目统一使用 `ContextBuilder.build_messages()`，不再保留 `PromptBuilder` 兼容层。
- V2.1 的 A0 保留完整 trajectory，A1 使用结构化语义摘要；日常运行仍默认使用 deterministic strategy。
- 语义压缩失败会显式记录并回退确定性摘要，但该 run 不具备 A1 验收资格。
- Semantic compaction 已通过 Stable V2.1 两轮独立 A/B；semantic strategy 仍需显式启用。

M5 已实现稳定的 Trace / Metrics 基础链路：

- 新增 `TraceRecorder`，将 `run_started`、`prompt_built`、`model_response`、`action_parsed`、`policy_decision`、`sandbox_exec_*`、`observation_created`、`artifact_written`、`context_compacted`、`approval_requested`、`run_completed`、`run_failed` 写入 `trace.jsonl`。
- 新增 `metrics.json` 落盘，保存 turns、bash actions、protocol errors、policy denials、context compactions、token/cache/latency 等 run 指标快照。
- Run 级 Prompt Cache 命中率按所有已上报请求的 hit/miss token 加权计算；供应商未上报缓存字段时为
  `null/unsupported`，与真实 `0%` 命中分开。Viewer 会从 trace 重新计算旧 run 的派生命中率，
  不修改历史 `metrics.json` 和 acceptance 原始证据。
- 每个结束的 run 生成带 schema version 的 `run_report.json` 和 `run_report.md`，统一关联 state、trace、metrics、workspace manifest 和 diff 证据。
- `minicc traces` 可列出本地 `.minicc/runs/<run_id>/trace.jsonl` 和 `metrics.json`。
- Skill Registry 和 Feedback Memory 暂按 experimental 保留，尚未声明对任务通过率或重复 I/O 的收益。

M6 已实现 eval runner 和只读 trace viewer：

- 新增 `minicc eval <path>`，读取 `case.yaml`，复制 fixture 到独立 run workspace，执行 agent，再运行确定性 assertions。
- Eval 支持 `command`、`file_exists`、`file_not_exists`、`file_contains`、`file_not_contains`、`diff_allowlist`、`diff_does_not_delete`、`no_source_diff`、`max_changed_files`、`metric_at_least`、`trace_contains_event`、`no_policy_violation` 等断言。
- 每次 eval 生成唯一 `suite_id`；不可变 manifest 与 JSON/Markdown/CSV 报告写入 `.minicc/suites/<suite_id>/`，不再覆盖上一次报告。
- run、suite、version entry 使用 schema v2 双向关联；旧记录按 `legacy/unknown` 只读展示，不用缺失字段推断失败。
- 新增 `minicc web`，使用 Python 标准库启动只读 trace viewer，支持 formal/development/history 分类、orphaned 识别，以及缺失可选 artifact 时降级显示。
- 新增 `minicc cleanup` retention 入口；默认 dry-run，只选择未被 suite、version index 或 acceptance 引用的旧 run，传入 `--apply` 才执行相同计划。

项目内置了一套 M6 capability suite，覆盖仓库理解、失败测试修复、小功能开发、回归测试、有限重构、环境配置修复、长日志调试和安全清理 8 类任务：

```bash
uv run minicc eval eval_cases/capability_suite_v1 --execute-local
```

不加 `--execute-local` 时会按默认 Docker sandbox 执行。评测结束后可查看：

```text
.minicc/suites/<suite_id>/manifest.json
.minicc/suites/<suite_id>/report.json
.minicc/suites/<suite_id>/report.md
.minicc/suites/<suite_id>/report.csv
```

如需查看 trace viewer，另开一个终端启动只读 Web 服务：

```bash
uv run minicc web
```

然后打开：

```text
http://127.0.0.1:8765
```

当前 Web viewer 不会随 `eval` 自动启动；它以 2 秒轮询方式读取 `.minicc/runs` 下已写入的
`trace.jsonl`、`metrics.json` 和 `artifacts/diff.patch`，适合在另一个终端中陪跑 `eval` 或单次 `run`。

运行记录同时会按 `minicc.yaml` 中的当前版本建立轻量索引：

```yaml
project:
  milestone: stable-v2.1.1
```

```text
.minicc/versions/<版本>/manifest.json
.minicc/versions/<版本>/<中文分类>/<中文标题>--<run_id>.json
```

版本索引只保存标题、分类和原始 run 路径，不移动或复制 `.minicc/runs`，因此不会破坏 checkpoint、
报告和历史证据路径。`minicc web` 会自动回填 V1.3/V2.0 的正式验收、开发预检、失败复现、
修复后重跑、回归验证和 Checkpoint 恢复记录；页面优先打开有记录的 `project.milestone`，当前版本
尚无记录时回退到最近的非空版本，并展示该版本的全部记录。需要临时归入其他版本时，可对
`run` 或 `eval` 使用 `--milestone <版本>`。

## V2.1 上下文压缩 A/B

V2.1 专项 case 位于 `eval_cases/compaction_suite_v1`。正式流程先用一个 case 各运行三次：

```bash
uv run minicc eval eval_cases/compaction_suite_v1 --case V21_C02_fix_failing_test --repeat 3 --context-variant a0
uv run minicc eval eval_cases/compaction_suite_v1 --case V21_C02_fix_failing_test --repeat 3 --context-variant a1
```

稳定后去掉 `--case` 扩展到三个 case，并独立复跑第二轮。两轮 suite 的 `report.json` 通过以下
命令生成最终判定；报告同时检查任务通过率、真实触发、prompt mean/max/n、关键事实保留率、
重复 I/O 和 cache 统计支持状态：

```bash
uv run minicc compaction-report \
  --a0 <round-1-a0-report.json> --a1 <round-1-a1-report.json> \
  --a0 <round-2-a0-report.json> --a1 <round-2-a1-report.json> \
  --output-dir acceptance/stable-v2.1/context-compaction-ab
```

Stable V2.1 的最终报告为 `PASS`：两轮 prompt mean 分别下降 9.27% 和 46.60%，任务通过率与
关键事实保留率均为 100%，重复 I/O 均满足门限。只有两轮得到同方向结论时报告才会输出
`PASS`；供应商没有返回缓存字段时显示 `unsupported`，不会伪装成 `0%` 或缓存收益。

## V2.1.1 Prompt Cache P0/P1

P0 保留 Stable V2.1 的 `system + 整体重建 user` 布局。P1 把固定 run context 放在第二条
消息，并将后续 action/observation 作为 assistant/user 消息追加；轨迹窗口移动或 compaction
summary 改变时允许显式重置动态部分。trace 只保存稳定前缀的 SHA-256、字符数和估算 token，
不保存 prompt 正文。

每轮先运行同一组 5 请求固定序列，再在真实 C02 上各运行 3 次。固定序列锁定前 2 次为
warm-up、后 3 次为 steady-state，并逐请求检查 P1 prompt token 不高于 P0。正式命令要求干净且
固定的 Git 提交；Provider 调用仍沿用 `provider.timeout_sec` 和 `provider.max_retries`，但实际
发生重试的请求会被标记并失去正式缓存结论资格，避免超时后的自预热被算成布局收益。
真实 C02 的可变 Feedback Memory 在本实验中关闭，保证 P0/P1 只改变消息布局。

```bash
uv run minicc cache-probe --cache-variant p0 --cache-sequence-id round-1 --repeat 5 --execution-order p0-first --milestone v2.1.1-development --release-gate
uv run minicc cache-probe --cache-variant p1 --cache-sequence-id round-1 --repeat 5 --execution-order p0-first --milestone v2.1.1-development --release-gate
uv run minicc eval eval_cases/capability_suite_v1 --case C02_fix_failing_test --cache-variant p0 --cache-sequence-id round-1 --execution-order p0-first --repeat 3 --milestone v2.1.1-development --release-gate
uv run minicc eval eval_cases/capability_suite_v1 --case C02_fix_failing_test --cache-variant p1 --cache-sequence-id round-1 --execution-order p0-first --repeat 3 --milestone v2.1.1-development --release-gate
```

第二轮使用 `round-2` 命名空间并倒置执行顺序为 P1 → P0，既避免复用上一轮的完整请求缓存，
也避免固定的先后顺序成为混杂变量；P0/P1 在同一轮内仍使用完全相同的动态序列。两轮共八份不可变证据通过
`minicc cache-report` 汇总。最终判定同时要求固定序列改善、真实 case 不退化、P1 任务通过率
不低于 P0、P0/P1 真实 C02 均为 3/3 PASS、稳定前缀估算 token 不下降、缓存字段完整、未缓存
token 实际下降，并明确区分 `unsupported`、真实 `zero_hit` 和 `nonzero_hit`。报告加载时会
校验 probe/suite manifest、逐项请求 SHA-256、run artifact hash、实际布局/namespace、run ID
唯一性和完整 run 证据；执行区间必须无重叠且固定探针先于真实 C02。最终 JSON/Markdown 会内嵌
精简的逐请求和逐 run 指标；未通过的汇总不会写进最终归档目录。

Stable V2.1.1 的最终报告为 `PASS`。`round-19`（P1→P0）与 `round-20`（P0→P1）均满足
固定序列改善、真实任务不退化、完整缓存字段、零 Provider 重试和证据哈希校验。P1 的真实
prompt 分别下降 14.08% 和 16.60%，未缓存 token 分别下降 31.75% 和 34.77%；归档只保留
精简入口与机器/人工可读报告，原始 run/suite 继续留在 `.minicc` 技术账本中。

## V2.1.2 Prompt Cache P1/P2

P2 历史实验使用 `epoch` 消息布局；当前配置名为更直白的
`append_until_compaction`：action/observation 只追加，不再因
`recent_turns` 滑窗逐回合删除旧消息；真正触及上下文预算时，一次压缩到 65% 目标水位，写入
summary checkpoint 并开始新的前缀段。每个请求都会记录分层 token 估算、相邻完整消息的
LCP、前缀哈希、冷启动、reset 原因、实际 hit/miss、理论可复用 token 和兑现率。压缩事件另行
记录压缩前后字符数、步骤范围、保留最近步骤数和事实保留率。Provider HTTP
client 在一个 run 内复用；连接、读写或协议类传输错误发生后丢弃失效 client，再按配置重试。

正式固定探针使用 12 个不同动态后缀。长稳定前缀来自仓库真实的
`src/minicc/evals/cache_probe.py` 代码片段，并在证据中记录来源、字符数和 SHA-256；它不是
重复请求或无语义 padding。P1 保留 V2.1.1 的 6-turn 滑窗作为基线，P2 保留整个 epoch。
每轮还对短任务 C02 和长日志任务 C07 各运行 3 次；C07 锁定为 9 个请求，沿
artifact→contract→binding test→source 的依赖链独立读取三份真实文件证据，第 8 个请求完成
全量 release check 并首次越过 P1 的滑窗边界，第 9 个请求给出 final。

```bash
uv run minicc cache-probe --cache-variant p1 --cache-sequence-id round-21 --repeat 12 --execution-order p1-first --milestone v2.1.2-development --release-gate
uv run minicc eval eval_cases/capability_suite_v1 --case C02_fix_failing_test --case C07_large_log_debugging --cache-variant p1 --cache-sequence-id round-21 --execution-order p1-first --repeat 3 --milestone v2.1.2-development --release-gate
uv run minicc cache-probe --cache-variant p2 --cache-sequence-id round-21 --repeat 12 --execution-order p1-first --milestone v2.1.2-development --release-gate
uv run minicc eval eval_cases/capability_suite_v1 --case C02_fix_failing_test --case C07_large_log_debugging --cache-variant p2 --cache-sequence-id round-21 --execution-order p1-first --repeat 3 --milestone v2.1.2-development --release-gate
```

第二轮使用独立 namespace 并倒置为 `p2-first`。八份不可变 probe/suite 报告由
`minicc cache-utilization-report` 汇总。未同时满足固定长序列与 C07 全链路命中率 70%、稳态
80%、兑现率 85%、prompt 膨胀不超过 10% 和任务/事实保留 100% 时，命令只打印失败门禁，
不创建 acceptance 目录。Provider 重试必须不超过配置且逐请求记录原因；重试请求按
`attempt_count × 最终 prompt` 的物理输入上界计费，有效 hit 强制为 0、全部计入 miss，不能
通过失败请求自预热提高成绩。固定序列通常要求全链路 miss 降低至少 40%；若 P1 全链路已
达到 80% 的饱和基线，则改用不受相对改善上限扭曲的严格替代门槛：P2 全链路仍须达到 80%、
稳态达到 90%，且绝对 miss 不得高于 P1。C07 的前 7 个请求中 P1/P2 尚未分叉，因此要求第
8 个请求起的 post-slide miss 降低至少 40%，全链路降低只作诊断。C02 的 prompt/miss 回归
在两轮倒序证据上合并计算，分别限制为 10%/15%，避免把单轮随机多一个工具动作误判为缓存
布局退化。每个 C07 run 必须同时证明严格递增的 1–9 request index、锁定 spec SHA-256 的 8-step
bash action shape（初始测试、artifact grep、三次独立读取、独立 edit、focused/full 验证），
以及恰好 2 个 post-slide 请求，避免比较不同语义阶段。固定探针的稳态排除预先锁定的 2 次
warm-up；真实任务从首次**单次 attempt**的实际
非零命中开始，并保留其后所有 miss。成功输出 `report.json`、`report.md` 和校验报告及八份输入证据的
`manifest.json`；八份入选 report/manifest 合并封装在单个 `evidence.json` 中，避免生成
零散输入目录，同时保证归档脱离本机 `.minicc` 后仍可复核。

V2.1.2 正式 eval 还锁定 canonical suite 路径和精确 C02/C07 矩阵。每个 run 在 agent 修改前
记录 workspace 基线的路径+内容摘要，并将 `case.yaml`、fixture 内容和来源路径组成的 authority
profile 贯通 workspace manifest、run report、suite report 与最终聚合门禁；两种布局、两轮和
全部 attempts 的 profile 必须一致，并逐文件通过 Git clean filter 与声明 commit 的 tree
object 对照；正式运行前后还拒绝 `skip-worktree`、`assume-unchanged`、ambient content-transform
attributes 和 fixture 额外空目录，避免隐藏变更或执行期间夹具漂移。由已哈希 trace 验证过的
逐请求 rows 与动作断言最小证据直接固化进 suite report；聚合与最终 evidence 不再二次读取活
trace 路径。

```bash
uv run minicc cache-utilization-report \
  --p1-probe <round-21-p1-probe.json> --p2-probe <round-21-p2-probe.json> \
  --p1-eval <round-21-p1-suite.json> --p2-eval <round-21-p2-suite.json> \
  --p1-probe <round-22-p1-probe.json> --p2-probe <round-22-p2-probe.json> \
  --p1-eval <round-22-p1-suite.json> --p2-eval <round-22-p2-suite.json> \
  --output-dir acceptance/stable-v2.1.2
```

V2.2 working memory 只接受显式 `source run` 中由模型声明的 workspace 文件行区间，不做环境式
自动检索。开发评测使用 `memory-eval` 完成 source/M0/M1 配对；正式门固定为 canonical
M01/M02/M03、Docker 摘要镜像、同一干净提交和每 case 3 次交替运行。三个 suite 完成后用
`memory-report` 验证 27 个 run 的哈希证据并生成四文件归档：

```bash
uv run minicc memory-eval eval_cases/memory_suite_v1/M01_service_contract_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-eval eval_cases/memory_suite_v1/M02_deploy_cli_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-eval eval_cases/memory_suite_v1/M03_validator_contract_follow_up --repeat 3 --execution-order alternating --milestone v2.2-acceptance --release-gate
uv run minicc memory-report --report <M01-report.json> --report <M02-report.json> --report <M03-report.json> --output-dir acceptance/stable-v2.2
```

正式目录只包含 `report.json`、`report.md`、`evidence.json`、`manifest.json`；原始运行保留在被
Git 忽略的 `.minicc/`，不会复制成大量 acceptance 文件。

## 快速开始

安装依赖并查看 CLI：

```bash
uv run minicc --help
```

运行测试：

```bash
uv run pytest
```

## 配置模型

> ⚠️ **密钥安全（务必先看）**：`minicc.yaml` 已被 `.gitignore` 忽略、**不进 git / GitHub**，
> 所以真密钥直接填在它的 route 里即可（用 `api_key:` 直填；`api_key_env:` 填环境变量名也行）。
> 仓库不自带 `minicc.yaml`——从被提交的通用模板 `minicc.example.yaml` 复制一份
> （`cp minicc.example.yaml minicc.yaml`）再按需改；模板只有占位变量名、没有真密钥。
> 若仍想走 `.env`：复制 `.env.example` → `.env`，把变量名对应的真密钥填进去。

把默认上游（`siliconflow`）的密钥 `SILICONFLOW_API_KEY` 改成你的即可；`MINICC_API_KEY` 只是某条 route 未声明 `api_key_env` 时的兜底。

```text
# 每条 route 的 api_key_env 指向这里的变量名；MINICC_API_KEY 是未声明 api_key_env 时的兜底
SILICONFLOW_API_KEY=替换成你的_api_key
# BAILIAN_API_KEY=替换成你的_api_key
# RELAY_PRIVATE_API_KEY=替换成你的_api_key
# MINICC_API_KEY=替换成你的_api_key
```

`minicc run` 会自动读取根目录 `.env`。如果系统环境变量里已经设置了同名配置，则系统环境变量优先。

**快速换上游（以阿里云百炼为例）：**

1. 在 `.env` 里填 `BAILIAN_API_KEY=你的百炼密钥`。
2. 切默认上游（二选一）：持久改 `minicc.yaml` 的 `default_provider: bailian`；或单次
   `MINICC_PROVIDER=bailian uv run minicc run "..."`（只对这次生效，不落盘）。
3. 若你账号的实际模型/地址与样例不同，改 `bailian` route 的 `model`（或 `base_url`），
   也可用 `MINICC_MODEL=...` 临时覆盖。
4. 验证：`uv run minicc models bailian` 能列出模型，就说明 key 与地址都通。

接入一个不在样例里的 OpenAI 兼容上游（例如你自己的 `/v1` 中转站）同理：在 `providers:` 下加一条
route，`base_url` 换成对方给的 `/v1` 地址、`api_key_env` 指向存放该 key 的变量名、`model` 填对方
支持的模型名；在 `.env` 里加对应的 `XXX_API_KEY=...`，再用 `default_provider` 或 `MINICC_PROVIDER`
指向它，最后用 `minicc models <route>` 验证。

模型服务地址、模型名、Docker sandbox、预算和上下文参数放在 `minicc.yaml`：

```yaml
sandbox:
  image: python:3.11-slim
  mode: locked
  cpus: "1"
  memory: 1g
  pids_limit: 256
  network: none

budget:
  max_bash_actions: 30
  max_seconds: 900
  max_turns: 0
  max_action_timeout_sec: 120

context:
  max_prompt_chars: 120000
  recent_turns: 6
  artifact_preview_chars: 12000
  prompt_layout: append_until_compaction

tooling:
  profile: baseline-bash
  max_parallel_tool_calls: 4
  max_tool_calls_per_step: 16

providers:
  siliconflow:
    base_url: https://api.siliconflow.cn/v1
    api_key_env: SILICONFLOW_API_KEY
    model: deepseek-ai/DeepSeek-V4-Pro
    json_mode: true
  bailian:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: BAILIAN_API_KEY
    model: qwen-plus
    json_mode: true
  relay-private:
    base_url: https://relay.example.com/v1
    api_key_env: RELAY_PRIVATE_API_KEY
    model: deepseek-chat

default_provider: siliconflow

# 可选：最外层降级链（在这些失败码上切下一 route）
# failover:
#   chain: [siliconflow, bailian, relay-private]
#   on: [QUOTA, AUTH, RATE_LIMIT, SERVER, TIMEOUT, TRANSPORT]
#   max_hops: 2

# V4 child / 子模型走哪条 route（model 可覆盖该 route 默认模型）
child:
  provider: siliconflow
  model: deepseek-ai/DeepSeek-V4-Flash

policy:
  require_approval_for_network: true
  deny_sudo: true
  require_approval_for_destructive: true

workspace:
  ignored_allowlist: []
```

`MINICC_PROVIDER`（覆盖 `default_provider`）、`MINICC_CHILD_PROVIDER`（覆盖 `child.provider`）、`MINICC_MODEL` / `MINICC_CHILD_MODEL`（覆盖 route 的 model）、`MINICC_PROVIDER_TIMEOUT_SEC`（覆盖默认 route 的 timeout）可用环境变量覆盖；密钥可直填 `minicc.yaml`（`api_key:` 或 `api_key_env:`）或放 `.env` / 系统环境（`api_key_env` 指变量名，`MINICC_API_KEY` 兜底）。

`tooling.profile` 选择工具面（`baseline-bash` 默认 / `hybrid-v3.6` / `multi-agent-v4`），也可用
`minicc run --profile <profile>` 单次覆盖；`max_parallel_tool_calls` 控制只读并行上限，
`max_tool_calls_per_step` 控制单轮 `tool_calls` 调用数量。

V4 子 Agent 使用 `child` 指定的 route（缺省回退 `default_provider`，可用 `child.model` 覆盖模型），主 Agent 用 `default_provider` route 的模型。使用 `minicc run --profile multi-agent-v4` 才会启用真实 child 模型编排。可用 `minicc models <route>` 列出某条 route 可用的模型（对目录外中转站也有效，配合 `--probe-key` 可临时试一把密钥）。

`workspace.ignored_allowlist` 只用于确实需要进入普通 run 的 ignored 项目文件，例如
`generated/runtime.json`。它不能放行 `.env`、`.minicc/`、`.workbuddy/`、虚拟环境、缓存或构建产物。

示例：

```bash
uv run minicc run "分析这个仓库并给出测试计划"
```

默认会复制 workspace 并在 Docker sandbox 中执行模型生成的 bash 命令。每次运行会保留：

```text
.minicc/runs/<run_id>/workspace
.minicc/runs/<run_id>/artifacts
.minicc/runs/<run_id>/workspace_manifest.json
.minicc/runs/<run_id>/artifacts/diff.patch
.minicc/runs/<run_id>/state.json
.minicc/runs/<run_id>/trace.jsonl
.minicc/runs/<run_id>/metrics.json
.minicc/runs/<run_id>/run_report.json
.minicc/runs/<run_id>/run_report.md
.minicc/suites/<suite_id>/manifest.json
.minicc/suites/<suite_id>/report.json
.minicc/suites/<suite_id>/report.md
.minicc/suites/<suite_id>/report.csv
.minicc/artifacts/<run_id>/manifest.json
```

查看 retention 计划不会删除任何内容：

```bash
uv run minicc cleanup --older-than-hours 168
```

确认列表后，只有显式添加 `--apply` 才会删除该次计划选中的未引用 run。被 suite、版本索引或
`acceptance/` 引用的 run 始终受保护。

若只做本地开发演示，可以显式开启本地执行：

```bash
uv run minicc run "运行测试并总结结果" --execute-local
```

本地执行也会默认使用 workspace copy。若确实想直接在当前目录执行，可加：

```bash
uv run minicc run "运行测试并总结结果" --execute-local --no-workspace-copy
```

这个模式只建议开发调试时使用。

对外部仓库做隔离演示时，使用 `--source-dir`；miniCC 会把源仓库复制到 run workspace，
并在结束时复核源仓库内容摘要。可以用一个或多个 `--verify-command` 绑定 Runtime
Completion Gate，模型只有在这些命令通过后才能完成：

```bash
uv run minicc run "修复代码并补充回归测试" \
  --source-dir D:\Code\MyHeiMaDianPing \
  --execute-local \
  --verify-command "mvn -q -Dtest=CacheDeleteMessageTest test"
```

Windows 本地执行会将 Maven/Gradle 命令交给原生 shell，并按 UTF-8 读取输出；构建产物
目录（例如 `target/`）不会进入 workspace diff。

## 审批与恢复

当模型发起 `ask`，或 policy 判断某个 bash action 需要人工审批时，run 会暂停并保存状态：

```text
.minicc/runs/<run_id>/state.json
```

批准 pending action：

```bash
uv run minicc approve <run_id> --yes
uv run minicc resume <run_id>
```

拒绝 pending action，或回答模型主动提出的 `ask`，并把内容作为 observation 交回模型：

```bash
uv run minicc deny <run_id> --reason "不要联网安装依赖"
uv run minicc resume <run_id>
```

`resume` 会重新启动执行环境，继续使用该 run 的 workspace 和 artifacts。

## Checkpoint 与恢复（V2.0）

V2.0 会在 run 开始、每个 observation 落盘后、等待审批和受控中断时创建版本化 checkpoint。
Checkpoint 同时保存 RunState、trajectory、workspace SHA256 指纹和 action 执行日志；恢复时会校验
run id、工作区路径、内容指纹和 checkpoint digest。若 action 可能已经执行但没有可靠完成记录，
系统会 fail-closed，拒绝自动重放。

受控中断演示：

```bash
uv run minicc run "完成一个小修改并验证" --interrupt-after-steps 1
```

从最新 checkpoint 恢复：

```bash
uv run minicc resume <run_id> --from-checkpoint
```

每个 run 的 checkpoint 保存在：

```text
.minicc/runs/<run_id>/checkpoints/checkpoint-0001.json
.minicc/runs/<run_id>/checkpoints/latest.json
```

## Action 协议

默认优先请求供应商原生 JSON mode（route 配置 `json_mode: true`）；若兼容供应商明确返回
400/422 不支持，Provider 会自动降级为文本响应，再由本地唯一顶层 JSON 解码器和 action schema
严格校验。评测 case 可通过 `workspace.writable_paths` 把 Docker 工作区根目录挂为只读，仅开放声明路径。

模型每轮必须只输出一个 JSON object，不能输出 Markdown。

执行命令：

```json
{"type":"bash","command":"pytest -q","timeout_sec":60,"purpose":"run tests"}
```

请求用户输入：

```json
{"type":"ask","question":"需要允许联网安装依赖吗？"}
```

结束任务：

```json
{"type":"final","answer":"任务完成，测试已通过。"}
```

协议错误会被转成 `protocol_error` observation，让模型按协议重试；连续错误超过阈值后 run 会失败。

默认 profile 下模型只有 `bash` / `ask` / `final` / `skill` 四种 action；扩展 action 由 profile 打开：

- `hybrid-v3.6` 增加 `tool_calls`，一次发起多个 `read`/`edit`/`write`/`bash` 调用：

```json
{"type":"tool_calls","calls":[{"id":"c1","tool":"read","arguments":{"path":"src/a.py"}},{"id":"c2","tool":"bash","arguments":{"command":"pytest -q"}}]}
```

- `multi-agent-v4` 增加 `delegate`，把任务交给一个或多个 child Agent：

```json
{"type":"delegate","tasks":[{"id":"scout-1","role":"scout","goal":"定位实现","capability_profile":"scout","depends_on":[],"output_schema":"investigation_report"}]}
```

`tool_calls` 与 `delegate` 和 `ask`/`skill`/`final` 不能在同一响应混用；未启用对应 profile 时它们会
收到 `protocol_error` 回喂。

## 目录结构

```text
src/minicc/
  cli.py              # CLI 入口：子命令、profile 选择、run/eval/resume/childrun/models
  config.py           # 配置合并（env > .env > minicc.yaml）+ providers 多 route/child/failover
  runtime.py          # V4 运行时契约：ChildCapabilities / CapabilityPolicy / ReadOnlyBashPolicy / WorkspaceWriteLease / AgentRuntime
  multi_agent.py      # V4：WorkflowCoordinator / ChildRunProvider / 标准工作流 / childrun 入口
  core/
    loop.py           # AgentLoop 编排（budget、tool_calls、delegate 分流）
    runner.py         # ModelTurnRunner：单次模型调用、usage 统计、action 解析
    context.py        # ContextBuilder：prompt 分层、预算检查、压缩摘要
    action_handler.py # final/ask/skill/bash 分流，policy 和 executor 调度
    protocol.py       # bash / skill / ask / final / tool_calls / delegate parser
    provider.py       # LlmFailure 稳定失败码 + OpenAI-compatible Adapter + ProviderRegistry
    retry.py          # per-route 重试执行器（run_with_retry / RetryingTurnProvider）
    failover.py       # 最外层降级链（ProviderFailoverChain）
    discovery.py      # minicc models 模型发现（有界 GET /models）
    state.py          # RunState / Observation / TrajectoryStep
    tooling.py        # ToolCallScheduler + read/edit/write/bash capability（expected_hash 版本校验）
    workflow.py       # workflow 辅助类型
    childrun.py       # childrun 子进程 JSONL transport
    session.py        # state 保存、审批（ask/approval）
    checkpoint.py     # 版本化 checkpoint
    lifecycle.py      # RunLifecycle 收尾
    verification.py   # CompletionVerifier（Runtime Completion Gate）
    ledger.py         # run/suite/version 技术账本（schema v2）
    run_catalog.py    # 版本索引
    project_context.py# repository inspector / MINICC.md project guide
    prompt.py         # 旧 prompt 兼容入口
  skills/
    registry.py       # SkillRegistry：多来源 catalog、YAML 校验、哈希冻结与按需加载
  memory/
    feedback.py       # FeedbackMemory：项目级 never/prefer/caution 规则选择
    working.py        # 显式 working memory：ground、snapshot、attach 与完整性校验
    compaction.py     # deterministic / semantic context compaction
  trace/
    recorder.py       # TraceRecorder：JSONL event 记录
    metrics.py        # metrics.json 快照落盘
    report.py         # run_report.json/.md
    transcript.py     # V4 transcript 投影（脱敏 transcript.jsonl/.md）
  evals/
    case.py           # eval case.yaml 读取与发现
    assertions.py     # eval 确定性断言
    runner.py         # eval suite/case 执行与报告
    cache_ab.py / compaction_ab.py / guidance_ab.py / memory_ab.py / meta_review_ab.py / release_report.py
  policy/
    base.py           # Policy / PolicyChain / PolicyDecision
    factory.py        # 按配置构建 PolicyChain
    command.py        # 危险命令策略
    path.py           # 敏感路径策略
    network.py        # locked mode 网络策略
    budget.py         # bash 次数和 timeout 策略
    approval.py       # 删除类动作审批策略
    capability.py     # V4 capability 权限策略
    readonly_bash.py  # V4 只读 bash 分类策略
  sandbox/
    artifact_store.py # 大输出 artifact 存储
    docker_runner.py  # Docker sandbox 启动、执行、清理
    local_runner.py   # 本地开发执行器
    observation.py    # command result -> Observation
    workspace.py      # run workspace copy 与 diff 生成
  meta/
    reviewer.py       # 离线 Meta Review
  server/
    app.py            # 标准库只读 trace viewer
tests/
  test_cli.py / test_loop.py / test_protocol.py / test_provider.py / test_policy.py / test_tooling.py
  test_workspace.py / test_docker_runner.py / test_observation.py
  test_multi_agent_v4.py / test_v36_tooling.py
docs/
  V3_5_PUBLIC_BENCHMARK_EXPERIMENT_PLAN.md
  V3_6_HYBRID_TOOLING_DESIGN.md
  V4_MULTI_AGENT_REFACTOR_PLAN.md
```

## 验收

V3.1.1 工程质量门禁：

```bash
uv sync --locked --all-groups
uv run ruff check src tests
uv run mypy src/minicc
uv run pytest --cov=minicc --cov-report=term-missing --cov-report=xml
uv build
```

上述命令与 `.github/workflows/ci.yml` 使用同一套锁文件和配置；全包分支覆盖率低于 50% 时测试
命令失败（`pyproject.toml` 的 `fail_under = 50`，早期 78% 门已在覆盖率现实压力下下调）。覆盖率
只能通过补测试提高，不能通过缩小统计范围达成。
V3.1.1 只验证工程质量门，不替换 `acceptance/stable-v3.0/` 与 `acceptance/stable-v3.1/` 中的
Provider-backed 正式能力证据。

Stable V1.0 验收命令：

```bash
uv run minicc --help
uv run minicc run --help
uv run minicc eval --help
uv run minicc web --help
uv run pytest -q
uv run minicc traces
```

版本化验收结果保存在 `acceptance/stable-v1.0/` 至 `acceptance/stable-v3.6/`，另有
`acceptance/experimental-v4/`。V3.0 的系统回归、Context、Memory、Resume 四维结论与逐 claim 证据入口见 `acceptance/stable-v3.0/report.md`；V3.1 Meta Review 的正式证据见 `acceptance/stable-v3.1/report.md`；V3.2 Skill/Feedback 指引 A/B 见 `acceptance/stable-v3.2/report.md`。V3.6 hybrid 工具面离线实现证据见 `acceptance/stable-v3.6/`，V4 多 Agent 确定性实现证据见 `acceptance/experimental-v4/`。

V3.1 Meta Review 验收命令：

```bash
uv run minicc meta-review <run_id>
uv run minicc meta-review-report --disabled-suite <a0-report.json> --enabled-suite <a1-report.json> --review <review-1> --review <review-2> --review <review-3> --output-dir acceptance/stable-v3.1 --release-gate
```

```bash
uv run minicc eval eval_cases \
  --case C01_repo_onboarding \
  --case C02_fix_failing_test \
  --case C03_add_cli_option \
  --case C04_add_regression_test \
  --case C09_hitl_destructive_command \
  --repeat 3 \
  --release-gate \
  --output-dir acceptance/stable-v1.3
```

该命令为每次运行保留独立的 state、trace、metrics、diff、run report 和 verifier report，并在输出目录生成汇总 JSON/Markdown 报告。
