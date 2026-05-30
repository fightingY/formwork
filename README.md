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
M4: Prompt builder、prompt cache 友好布局、context budget、compression
M5: Skill Registry、Feedback Memory、Trace events、Metrics
M6: Eval runner、Web trace viewer、文档与面试示例
```

## 当前进度：M2

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
    loop.py           # Minimal Agent Loop
    prompt.py         # M1 简版 prompt builder
    protocol.py       # bash / ask / final action parser
    provider.py       # OpenAI-compatible Provider Adapter
    state.py          # RunState / Observation / TrajectoryStep
  sandbox/
    artifact_store.py # 大输出 artifact 存储
    docker_runner.py  # Docker sandbox 启动、执行、清理
    observation.py    # command result -> Observation
    workspace.py      # run workspace copy 与 diff 生成
tests/
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

当前 M2 验收命令：

```bash
uv run minicc --help
uv run pytest
```

预期：CLI 正常显示，测试全部通过。
