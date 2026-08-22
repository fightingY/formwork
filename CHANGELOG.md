# Changelog

本文件记录 miniCC 的稳定版本变更。格式参考 Keep a Changelog，版本号遵循语义化版本。

## [Unreleased]

### Added

- V4 可验证多 Agent Harness（experimental）：`delegate` action 与 `WorkflowCoordinator`，
  角色 `scout`/`planner`/`worker`/`reviewer`，单一 `WorkspaceWriteLease` 写者，以及
  `CapabilityPolicy` / `ReadOnlyBashPolicy` 权限边界。
- `minicc childrun` 子进程与 in-process 两种 child 后端，通过 stdin/stdout JSONL 通信。
- 不可变 `trace.jsonl` 到脱敏 `transcript.jsonl` / `transcript.md` 的可读投影。
- `child_provider` 子模型配置（`MINICC_CHILD_MODEL`，旧别名 `MINICC_FAST_MODEL`），未设置时回退主模型。

### Changed

- 移除无意义的 `max_turns` 预算；改由 `max_seconds`（wall-clock，Loop 内强制）+
  `max_bash_actions` + `max_action_timeout_sec` 约束。
- 删减 eval/acceptance 侧大量哈希校验与 `source_lock.yaml`，简化证据链操作。
- 覆盖率硬门由 78% 下调至 50%（对应 `pyproject.toml` 的 `fail_under = 50`）。

### Boundaries

- V4 仍为 experimental，未升 stable；不声明真实 Provider 多 Agent 成功率。
- 正式只读 child 的 Docker read-only mount 证据仍需 Docker 集成环境。

## [3.6.0] - 2026-08-21

### Added

- Structured `read`, `edit`, `write`, and `bash` hybrid tooling with strict workspace and version contracts.
- Ordered multi-tool protocol, bounded read parallelism, exclusive barriers, abort handling, durable tool trace, and explicit `baseline-bash` / `hybrid-v3.6` profiles.
- Deterministic V3.6 M4 offline evidence archive with zero provider calls.

### Validated

- Full CI-equivalent quality gate: 367 passed, 2 skipped, 78.46% coverage, Ruff, mypy, and package build passed.

### Boundaries

- Formal M5 real-model A/B is not claimed by the offline archive; the default profile remains `baseline-bash`.

## [3.5.0] - 2026-08-20

### Added

- Docker 生命周期治理：启动失败即时回滚删除容器；容器内命令超时销毁容器并标记 run 失败但保留
  workspace 与 artifacts；轻量启动前置检查。
- 六个公开题库 case 的 source/verifier 合同、离线 18-run 聚合器、恢复矩阵和归档索引。

### Validated

- 真实 Docker 集成测试（有 `MINICC_DOCKER_INTEGRATION` 环境变量门控）：3 passed。

## [3.4.0] - 2026-08-19

### Added

- 最小真实项目评测闭环：初始失败验证、独立最终 verify、verdict、验证 artifact、自动 workspace 清理。
- 从 MyHeiMaDianPing 抽取的三个最小 Java 任务 fixture 和 mutation 回归测试任务。

## [3.2.0] - 2026-08-12

### Added

- 增加目标相关 Skill/Feedback 确定性选择、带哈希的 Skill 正文注入、选择 trace/metrics 和正式 A/B 报告门。
- A1 的反馈来源绑定到 eval workspace 中的固定提交，A0 显式禁用两类指引。

### Validated

- 固定 G01 上 A0/A1 各 3/3 PASS；A1 精确选择相关 Skill/Feedback，Bash 动作由 13 降至 6，prompt tokens 由 16,683 降至 8,162。

### Boundaries

- 不声明自动反馈提取、环境式检索、RAG 或跨任务质量提升。

## [3.1.1] - 2026-08-12

### Added

- 增加 Python 3.11/3.12 GitHub Actions 质量门。
- 增加 Ruff、mypy、全包分支覆盖率和 78% 最低覆盖率配置；建门实测基线为 78.60%。
- 增加贡献指南、安全问题报告流程和锁文件构建验证。

### Fixed

- 统一 README、包元数据和 CLI 的当前版本口径。

### Unchanged

- 不改变 Stable V3.0/V3.1 能力实现、Provider 正式运行或 acceptance 证据。

## [3.1.0] - 2026-08-12

- 将离线 Meta Review 升格为稳定能力，正式验收为 20/20 条件通过。
- 保留“不声明应用建议后任务质量收益”的明确边界。

## [3.0.0] - 2026-08-02

- 聚合系统、Context、Memory、Resume 四维正式发布证据。
- 固定系统矩阵 15/15 PASS，四维证据共定位 67 个 runs。