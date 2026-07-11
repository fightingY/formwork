# miniCC

miniCC 是一个面向面试展示的 Bash-first CodeAct Agent Harness。

它的目标不是复刻完整 Claude Code，也不是堆很多工具，而是把 Coding Agent 背后的工程层拆清楚：模型负责推理和生成 action，harness 负责协议校验、Provider 适配、执行编排、状态管理、策略、安全、上下文、trace 和 eval。

## 项目定位

一句话概括：

```text
miniCC 用极简 bash / ask / final action space 承载模型智能，用 harness 把执行过程变得可控、可观测、可回归。
```

当前项目按 6 个里程碑推进：

```text
M1: uv 项目骨架、Provider Adapter、Action Protocol、Minimal Agent Loop
M2: workspace copy、Docker sandbox、Observation contract、Artifact store
M3: PolicyChain、Command/Network/Budget policy、ask/approval/resume
M4: Prompt builder、prompt cache 友好布局、context budget、experimental compression
M5: Experimental Skill/Feedback Memory、Trace events、Metrics
M6: Eval runner、Web trace viewer、文档与面试示例
```

## 当前稳定版本：Stable V1.0

Stable V1.0 只声明已经通过基线验收的 Agent Loop、workspace、Docker sandbox、Policy、trace、metrics、eval 和只读 Web Viewer。Semantic compaction、Skill Registry 和 Feedback Memory 虽然已有原型及单元测试，但在完成独立效果实验前统一视为 experimental，不计入 Stable V1.0 的稳定能力。

M1 已实现基础闭环：

- 使用 `uv` 管理 Python 项目。
- 提供 `minicc` CLI 入口。
- 实现 OpenAI-compatible Provider Adapter。
- 归一化模型 usage 和 prompt cache 指标。
- 实现严格 JSON action 协议，只允许 `bash`、`ask`、`final`。
- 实现最小 Agent Loop：构建 prompt、调用模型、解析 action、处理 bash/ask/final。
- 提供可注入 executor，方便后续替换为 Docker sandbox。
- 补充单元测试覆盖 Provider、Protocol 和 Loop。

M2 已实现执行与结果治理层：

- 每个 run 会复制当前 workspace 到 `.minicc/runs/<run_id>/workspace`。
- workspace copy 会忽略 `.git`、`.venv`、`node_modules`、`.minicc`、`.env` 等目录或敏感文件。
- run workspace 内会初始化 git baseline，任务结束后生成 `artifacts/diff.patch`。
- 默认使用 Docker sandbox 执行 bash action。
- Docker 容器默认禁网，并限制 CPU、内存、PID、capabilities 和 no-new-privileges。
- 命令结果会标准化为 `Observation`：`command_result`、`no_output`、`command_error`、`timeout` 等。
- 超长 stdout/stderr 会写入 artifact，prompt 中只保留 preview、artifact id 和路径。

M3 已实现策略中间件和 HITL 基础链路：

- 实现 `PolicyChain`，bash action 在进入 executor 前必须先经过策略链。
- 实现 `CommandPolicy`，拦截 `sudo`、危险 `rm -rf /`、`shutdown`、`mkfs`、`mount` 等高危命令。
- 实现 `PathPolicy`，拦截 `/mnt`、`/var/run/docker.sock`、`/root/.ssh` 等敏感路径。
- 实现 `NetworkPolicy`，locked mode 下对 `curl`、`wget`、`git clone`、`pip install`、`npm install` 等联网动作要求审批或拒绝。
- 实现 `BudgetPolicy`，限制 bash action 次数，并把超长 timeout 改写到配置上限。
- 实现 `ApprovalPolicy`，对删除类高风险动作触发人工审批。
- `ask` 和 `require_approval` 会让 run 进入 `waiting_approval`，并把状态保存到 `state.json`。
- 新增 `approve`、`deny`、`resume` CLI 命令，支持 Stop and Resume 风格的审批恢复。

M4 已实现稳定的上下文构建基础链路：

- 新增 `ContextBuilder`，统一承载 prompt assembly、context budget 和 compression 逻辑。
- Prompt 按 Stable Prefix / Dynamic Context 分层组装，把 action 协议、policy 摘要和 observation contract 固定前置。
- 实际项目统一使用 `ContextBuilder.build_messages()`，不再保留 `PromptBuilder` 兼容层。
- Semantic compaction 代码暂按 experimental 保留，需通过后续 A/B 里程碑后才能作为稳定能力对外声明。

M5 已实现稳定的 Trace / Metrics 基础链路：

- 新增 `TraceRecorder`，将 `run_started`、`prompt_built`、`model_response`、`action_parsed`、`policy_decision`、`sandbox_exec_*`、`observation_created`、`artifact_written`、`context_compacted`、`approval_requested`、`run_completed`、`run_failed` 写入 `trace.jsonl`。
- 新增 `metrics.json` 落盘，保存 turns、bash actions、protocol errors、policy denials、context compactions、token/cache/latency 等 run 指标快照。
- 每个结束的 run 生成 `run_report.json` 和 `run_report.md`，统一关联 state、trace、metrics 和 diff 证据。
- `minicc traces` 可列出本地 `.minicc/runs/<run_id>/trace.jsonl` 和 `metrics.json`。
- Skill Registry 和 Feedback Memory 暂按 experimental 保留，尚未声明对任务通过率或重复 I/O 的收益。

M6 已实现 eval runner 和只读 trace viewer：

- 新增 `minicc eval <path>`，读取 `case.yaml`，复制 fixture 到独立 run workspace，执行 agent，再运行确定性 assertions。
- Eval 支持 `command`、`file_exists`、`file_not_exists`、`file_contains`、`file_not_contains`、`diff_allowlist`、`diff_does_not_delete`、`no_source_diff`、`max_changed_files`、`metric_at_least`、`trace_contains_event`、`no_policy_violation` 等断言。
- Eval 报告写入 `.minicc/runs/eval_reports/eval_report.json` 和 `eval_report.md`。
- 新增 `minicc web`，使用 Python 标准库启动只读 trace viewer，支持自动刷新、run 搜索、事件筛选、timeline、metrics 和 diff 查看。

项目内置了一套 M6 capability suite，覆盖仓库理解、失败测试修复、小功能开发、回归测试、有限重构、环境配置修复、长日志调试和安全清理 8 类任务：

```bash
uv run minicc eval eval_cases/capability_suite_v1 --execute-local
```

不加 `--execute-local` 时会按默认 Docker sandbox 执行。评测结束后可查看：

```text
.minicc/runs/eval_reports/eval_report.json
.minicc/runs/eval_reports/eval_report.md
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

项目根目录已经提供 `.env`，直接把里面的 `MINICC_API_KEY` 改成你的模型密钥即可。

```text
MINICC_BASE_URL=https://api.siliconflow.cn/v1
MINICC_API_KEY=替换成你的_api_key
MINICC_MODEL=deepseek-ai/DeepSeek-V4-Pro
MINICC_TEMPERATURE=0
```

`minicc run` 会自动读取根目录 `.env`。如果系统环境变量里已经设置了同名配置，则系统环境变量优先。

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
  max_turns: 12
  max_bash_actions: 30
  max_seconds: 900
  max_action_timeout_sec: 120

context:
  max_prompt_chars: 120000
  recent_turns: 6
  artifact_preview_chars: 12000

provider:
  base_url: https://api.siliconflow.cn/v1
  model: deepseek-ai/DeepSeek-V4-Pro
  temperature: 0
  stream: false
  include_usage: true

policy:
  require_approval_for_network: true
  deny_sudo: true
  require_approval_for_destructive: true
```

其中 `MINICC_BASE_URL`、`MINICC_MODEL`、`MINICC_TEMPERATURE` 可以通过环境变量覆盖 `minicc.yaml`；`MINICC_API_KEY` 只建议放在 `.env` 或系统环境变量里，不写入 `minicc.yaml`。

示例：

```bash
uv run minicc run "分析这个仓库并给出测试计划"
```

默认会复制 workspace 并在 Docker sandbox 中执行模型生成的 bash 命令。每次运行会保留：

```text
.minicc/runs/<run_id>/workspace
.minicc/runs/<run_id>/artifacts
.minicc/runs/<run_id>/artifacts/diff.patch
.minicc/runs/<run_id>/state.json
.minicc/runs/<run_id>/trace.jsonl
.minicc/runs/<run_id>/metrics.json
.minicc/runs/<run_id>/run_report.json
.minicc/runs/<run_id>/run_report.md
```

若只做本地开发演示，可以显式开启本地执行：

```bash
uv run minicc run "运行测试并总结结果" --execute-local
```

本地执行也会默认使用 workspace copy。若确实想直接在当前目录执行，可加：

```bash
uv run minicc run "运行测试并总结结果" --execute-local --no-workspace-copy
```

这个模式只建议开发调试时使用。

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

## Action 协议

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

## 目录结构

```text
src/minicc/
  cli.py              # CLI 入口
  config.py           # 环境变量配置
  core/
    loop.py           # 只保留 Agent Loop 编排
    runner.py         # 模型调用、usage 统计、action 解析
    context.py        # M4 ContextBuilder：prompt 分层、预算检查、压缩摘要
    action_handler.py # final/ask/bash 分流，policy 和 executor 调度
    session.py        # state 保存、审批请求和审批结果应用
    prompt.py         # 旧 prompt 模块兼容入口，仅导出 ContextBuilder / SYSTEM_PROMPT
    protocol.py       # bash / ask / final action parser
    provider.py       # OpenAI-compatible Provider Adapter
    state.py          # RunState / Observation / TrajectoryStep
  skills/
    registry.py       # SkillRegistry：读取 SKILL.md catalog
  memory/
    feedback.py       # FeedbackMemory：读取反馈规则
  trace/
    recorder.py       # TraceRecorder：JSONL event 记录
    metrics.py        # metrics.json 快照落盘
  evals/
    case.py           # eval case.yaml 读取与发现
    assertions.py     # eval 确定性断言
    runner.py         # eval suite/case 执行与报告
  server/
    app.py            # 标准库只读 trace viewer
  policy/
    base.py           # Policy / PolicyChain / PolicyDecision
    factory.py        # 根据配置构建完整 PolicyChain
    command.py        # 危险命令策略
    path.py           # 敏感路径策略
    network.py        # locked mode 网络策略
    budget.py         # bash 次数和 timeout 策略
    approval.py       # 删除类动作审批策略
  sandbox/
    artifact_store.py # 大输出 artifact 存储
    docker_runner.py  # Docker sandbox 启动、执行、清理
    local_runner.py   # 本地开发执行器
    observation.py    # command result -> Observation
    workspace.py      # run workspace copy 与 diff 生成
tests/
  test_policy.py
  test_docker_runner.py
  test_observation.py
  test_workspace.py
  test_loop.py
  test_protocol.py
  test_provider.py
docs/
  AI_IMPLEMENTATION_SPEC.md
  INTERVIEW_PLAYBOOK.md
```

## 验收

Stable V1.0 验收命令：

```bash
uv run minicc --help
uv run minicc run --help
uv run minicc eval --help
uv run minicc web --help
uv run pytest -q
uv run minicc traces
```

版本化验收结果保存在 `acceptance/stable-v1.0/`。真实模型 capability suite 从 V1.1 开始按固定 provider、模型、温度和预算单独验收，不属于 V1.0 基线恢复的成功率声明。
