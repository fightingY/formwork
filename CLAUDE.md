# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在本仓库工作提供指引。

## 项目是什么

miniCC（`mini-claude-code`）是一个 **Bash-first 的 CodeAct Agent Harness**——面向面试展示的、对 coding agent 背后工程层的最小化复刻。模型只输出一小撮 action（`bash` / `ask` / `final` / `skill`，外加实验性的 `tool_calls` 和 `delegate`），harness 负责协议校验、Provider 适配、执行编排、状态管理、策略、安全、上下文/prompt-cache、trace 与 eval。当前包版本为 `3.7.0.dev0`；`minicc.yaml` 里的 `project.milestone` 记录发布里程碑（当前 `v4.1`）。

文档很全但是中文、且多为历史验收叙述。有用的几份：`README.md`（验收/证据）、`miniClaudeCode.md`（按 ETCLOVG 七层组织的面试讲稿）、`docs/V3_5_PUBLIC_BENCHMARK_EXPERIMENT_PLAN.md`、`docs/V3_6_HYBRID_TOOLING_DESIGN.md`、`docs/V4_MULTI_AGENT_REFACTOR_PLAN.md`（V3.5–V4 实施方案）、`docs/V4_1_PROVIDER_REFACTOR_PLAN.md`（V4.1 实施方案）、`STABLE_V1_MILESTONE_ROADMAP.md`（路线图）。本文件是工作速查。

## 开发命令

需要 Python 3.11 或 3.12、Git、`uv`。只有真实执行（非 `--execute-local`）和 Docker 集成测试才需要 Docker。

```bash
uv sync --locked --all-groups            # 安装运行时 + 开发依赖（ruff, mypy, pytest, pytest-cov, types-pyyaml）

uv run ruff check src tests              # lint（配置在 pyproject.toml；line-length 100）
uv run mypy src/minicc                   # 类型检查（严格：disallow_untyped_defs、no_implicit_optional）

uv run pytest                            # 全量测试（testpaths=tests, pythonpath=["src","."]）
uv run pytest tests/test_server.py       # 单个文件
uv run pytest -k "cache_probe"           # 单个测试 / 按名字过滤
uv run pytest --cov=minicc --cov-report=term-missing   # 覆盖率；当前硬门是 fail_under=50（pyproject.toml）

MINICC_DOCKER_INTEGRATION=1 uv run pytest tests/test_docker_runner_integration.py -q   # Docker 集成测试（有环境变量门控）

uv build                                 # 构建 sdist/wheel
```

以上与 `.github/workflows/ci.yml` 一致（Python 3.11 + 3.12；覆盖率只在 3.11 跑；Docker 集成只在 3.11 跑）。覆盖率硬门从 78% 下调后，权威闸门是 `pyproject.toml` 里的 `fail_under = 50`。不要靠缩小统计范围（比如排除 `cli.py` 等低覆盖模块）来抬覆盖率，要通过补测试。

## CLI 概览（`uv run minicc …`）

- `run "<goal>"` —— 让 agent 循环执行一个目标。常用参数：`--source-dir`（把外部仓库隔离复制进来）、`--execute-local`（改成宿主机执行而非 Docker）、`--no-workspace-copy`、`--verify-command`（可重复，绑定 Runtime Completion Gate）、`--profile {baseline-bash,hybrid-v3.6,multi-agent-v4}`、`--interrupt-after-steps N`、`--follow-up-from <run_id>`。
- `eval <cases_dir>` —— 跑 eval case；`--repeat N`、`--case NAME`（可重复）、`--execute-local`、`--release-gate`。
- `resume / approve / deny <run_id>` —— Stop-and-Resume 的 HITL 闭环（`resume --from-checkpoint` 改为从 checkpoint 恢复）。
- `session new|list|show|rename|switch|resume …` —— V5 会话骨架（experimental），`chat [--session <id>] [--port N]` 起会话 REPL 或纯标准库 Web 聊天（`--port` 走 `server/chat.py` 的 SSE + 单页前端）。每轮仍是一个 `run_id`，`transcript.jsonl` 为唯一事实源。
- `models <route> [--probe-key KEY] [--json]` —— 对某条 route 做有界 `GET /models` 模型发现（对目录外中转站也可列模型）。
- `traces`、`transcript <trace.jsonl>`、`web`（只读 trace viewer）、`cleanup`（默认 dry-run，`--apply` 才删）、`meta-review <run_id>`、`childrun`（V4 子进程传输的内部命令）。
- 实验/验收报告命令：`release-report`、`compaction-report`、`cache-probe`、`cache-report`、`cache-utilization-report`、`memory-eval`、`memory-report`、`guidance-report`、`meta-review-report`。

## 配置分层

`load_settings()`（`src/minicc/config.py`）按优先级合并：系统 env > `.env`（简易 dotenv 加载，已存在则不覆盖）> `minicc.yaml`。上游是 `providers:` dict（key 即 route 名，每条 route 有 `base_url`/`api_key`/`api_key_env`/`model`/`headers`/`timeout_ms`/`retry_policy`）+ `default_provider` + 可选 `failover` 降级链 + `child` 子模型 route + 可选 `aux` 辅助模型。`MINICC_PROVIDER`/`MINICC_CHILD_PROVIDER`/`MINICC_MODEL`/`MINICC_CHILD_MODEL`/`MINICC_PROVIDER_TIMEOUT_SEC` 覆盖对应项。密钥：`minicc.yaml` 已被 `.gitignore` 忽略、不进 git，真密钥直接填 route 的 `api_key:`（`api_key_env:` 填环境变量名；找不到同名变量时把它本身当密钥），`MINICC_API_KEY` 兜底；被提交的 `minicc.example.yaml` 是通用模板、只留 `your_api_key_here` 占位、绝不放真密钥。

## 架构大图

运行管线是一个循环，拆在 `src/minicc/` 下：

1. **`core/loop.py`（`AgentLoop`）** —— 编排：压缩上下文（`context.maybe_compact`）→ 拼消息 → `turn_provider.next_turn`（经重试/降级扩展点）→ 交给 `ActionHandler` → checkpoint → 循环直到 status 离开 `running`；失败由 `except ProviderError`（读 `failure.code`）收敛。
2. **`core/provider.py`** —— V4.1 的 provider 边界：`LlmFailure` 稳定失败码、`OpenAICompatibleProvider`（单次可见 attempt，只报事实、不重试/不降级）、`ProviderRegistry`（按 route 构造 adapter）。
3. **`core/runner.py`（`ModelTurnRunner`）** —— 单次模型回合；负责 cache/token 指标累计（`_accumulate_usage` 等）。大部分 cache 分层计算都在这里。
4. **`core/retry.py` + `core/failover.py`** —— 失败步骤扩展点上两件独立编排：per-route 重试（有界退避+抖动、尊重 `Retry-After`、落 `llm/retry` 事件）与最外层降级链（按 `failover.on` 准入码跨 route，落 `failover/hop` 事件）。
5. **`core/discovery.py`** —— `minicc models <route>` 的模型发现：有界 `GET {base_url}/models`，401/403→`AUTH`，其余解析失败→`DISCOVERY_FAILED`。
6. **`core/protocol.py`** —— 严格 action 解析器。只接受一个 `bash|skill|ask|final|tool_calls|delegate` 类型的 JSON object；会回退适配 markdown/`<function>` 外壳。`ProtocolError` 作为 `protocol_error` observation 喂回模型。
7. **`core/action_handler.py`（`ActionHandler`）** —— 分流：`final`（视情况跑 `CompletionVerifier` gate、落地声明的 memory 引用、完成）、`ask`（HITL）、`skill`（加载冻结的 skill 正文）、`bash`（policy 链 → executor）。也实现 `_record_io_action` 里的「重复 I/O 守卫」。
8. **`core/context.py`（`ContextBuilder`）** —— 分层 prompt 组装（stable prefix + 动态 trajectory）、预算检查、压缩（默认 deterministic，可选 semantic）。prompt layout 有 `rebuild`/`append`/`epoch`/`append_until_compaction`。
9. **`policy/`（`PolicyChain`）** —— 有序策略（command/path/network/budget/approval + V4 的 capability/readonly-bash），返回 `deny`/`require_approval`/`rewrite`/`allow`。由 `policy/factory.py` 按配置构建。
10. **`sandbox/`** —— `workspace.py`（复制 + `diff.patch`）、`docker_runner.py`、`local_runner.py`、`observation.py`（命令结果归一成 `Observation`）、`artifact_store.py`。
11. **`trace/` + `core/ledger.py` + `core/run_catalog.py`** —— `events.jsonl` 是 canonical EventLog；`trace.jsonl` 是由其 `trace/event` 投影物化的兼容证据视图，另有 `metrics.json`、`run_report.json/.md`、suite manifest/report、版本索引。schema v2、不可变、SHA-256 锚定。

V5 会话层（experimental）叠在 run/eval **之上**（Project → Session → Turn → Run → Message），不改变上面的 loop：

12. **`core/session_store.py`（`SessionStore`）** —— `.minicc/sessions/<id>/{session.json, transcript.jsonl, runs/<run_id>/}`；`transcript.jsonl` append-only、`seq` 单调、`role:user/assistant`，是唯一事实源。
13. **`core/session_engine.py`（`SessionEngine`）** —— 可重入 turn loop：注入 `loop_factory` 组装 `AgentLoop`，注入 `on_approval` 切同步/延迟审批，`on_turn_end` 是 V5.1 L1 蒸馏 seam。每轮 = 一个 `run_id`，仍落 trace/metrics。
14. **`server/chat.py`（`serve_chat` / `ChatBroker`）** —— 纯标准库 `ThreadingHTTPServer` + SSE 单页聊天前端；turn 走 `submit_turn`/`resolve_turn` 纯函数，审批/deny 走 HTTP endpoint。steer 是 best-effort 追加 redirect。
15. **`memory/`（V5.1 L0→L3 记忆金字塔，experimental）** —— `l1.py`（`L1Distiller`/`MemoryStore`：每项目一个 `.minicc/memory/<project-hash>.db`，FTS5 检索 + 可选 embedding/RRF）、`escalation.py`（`PersonaEscalator`/`ScenarioEscalator`，阈值触发 L3 persona / L2 scenario）、`dedup.py`（`L1Deduper` store/skip/update/merge）、以及保留的 `feedback.py`（手写 L3 种子）、`working.py`（**已删四重哈希，改为失败跳过**）、`compaction.py`。`MemoryTurnHook` 挂在 `SessionEngine.on_turn_end`；双轨注入：L2/L3 进 system 缓存轨（stable prefix 尾部）、L1 进每轮 `<relevant-memories>` 块。全程优雅降级、失败不阻断。

**安全 = 双模式分工**：会话在真实工作目录直跑 + 审批链 + git 回滚；run/eval 的隔离拷贝（快照复制 + `diff.patch`）保留为块状模式专用。

有**三套 tooling profile**，由 `tooling.profile` 选择（见 `core/tooling.py`、`core/loop.py`、`multi_agent.py`、`runtime.py`）：

- **`baseline-bash`**（默认）—— 只有 `bash`/`ask`/`final`/`skill`，走 `BashExecutor`。
- **`hybrid-v3.6`** —— 增加 `tool_calls` action（`read`/`edit`/`write`/`bash`）和 `ToolCallScheduler`：`read` 可并行，`edit`/`write`/`bash` 是排他屏障；FS 工具用 `expected_hash` 做乐观版本校验（`core/tooling.py`）。
- **`multi-agent-v4`** —— 增加 `delegate` action → `WorkflowCoordinator`（`multi_agent.py`）按依赖顺序、有界并发地跑子任务（角色 `scout`/`planner`/`worker`/`reviewer`），用 `WorkspaceWriteLease` 保证单写者，带能力画像（`runtime.py`）。子任务通过 `minicc childrun` 的 stdin/stdout JSONL 运行。用 `minicc run --profile multi-agent-v4` 才会启用真实子模型编排。

`evals/` 放 eval runner（`runner.py`）、case 发现（`case.py`）、断言（`assertions.py`），以及每个实验一个报告构建模块（cache/compaction/memory/guidance/meta-review/release）。`skills/`、`memory/`（l1/escalation/dedup/feedback/working/compaction）、`meta/`（离线 Meta Review）、`server/`（`web.py` 只读 trace viewer + `chat.py` Web 聊天）是更窄的子系统。

## Eval、证据与发布治理

`eval_cases/<suite>/<case>/` 是 `case.yaml` + `fixture/`。`minicc eval` 把 fixture 复制进隔离的 run workspace，跑 agent，然后执行确定性断言。一切都留证据且不可变：

- run 产物在 `.minicc/runs/<run_id>/` 下：`state.json`、`trace.jsonl`、`metrics.json`、`workspace/`、`artifacts/`、`workspace_manifest.json`、`run_report.json/.md`、`checkpoints/`。
- suite 在 `.minicc/suites/<suite_id>/`；版本索引在 `.minicc/versions/`。
- `--release-gate` 要求干净的不可变 Git 提交、Docker 执行、`--repeat >= 3`，并在前后各校验一次 Git 状态（还会拒绝 `skip-worktree`/`assume-unchanged`/content-transform 属性）。发布/聚合报告命令（`release-report`、`cache-report` 等）会加载并对既有报告做哈希校验，失败时不写 `acceptance/` 目录。

不调 Provider 的确定性验证：`python eval_cases/real_project_suite_v1/*/fixture/verify.py`，或 `uv run minicc release-report` + `uv run pytest -q tests/test_release_report.py tests/test_server.py`。

## 注意点与约定

- **Action 协议很严格。** 模型必须只输出一个 JSON object（不能是 markdown）。Provider 先走 `json_mode`，被拒（`400/422`）后降级为文本再按「单一顶层 JSON」解码。见 `core/protocol.py`。
- **Runtime Completion Gate**（`--verify-command`，或 case 的 `completion_gate` / `command` 断言）在模型请求 `final` 时跑预绑定的验证命令；失败会回喂模型，不通过就不能 `complete`。
- **Windows 宿主机**（主要开发环境）：`cli.py::_reconfigure_std_streams` 强制 UTF-8 以扛 GBK 控制台；`--execute-local` 会把 Maven/Gradle 交给原生 shell 并按 UTF-8 读输出。Docker 集成测试用 `MINICC_DOCKER_INTEGRATION=1` 门控，主要在 Linux 跑。本机没有 `gh` CLI——查 CI 日志用 `scripts/fetch_ci_logs.py`。
- 新能力先标 `experimental`，只有在有确定性测试 + `acceptance/` 下的真实模型验收归档后才能升为 `stable`。不要覆盖既有 suite/report 文件，不要把失败或中断的尝试混进正式 acceptance。
- 测试在 `tests/` 下与模块一一对应（`tests/test_*.py`）。`pythonpath = ["src", "."]` 已配好，直接 `from minicc import …` 即可。
