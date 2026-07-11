# Stable V1.0 验收记录

验收日期：2026-07-11

基线：`stable-v1`，起点 `8f19cd3`

发布版本：`minicc 1.0.0`

验收 tag：`stable-v1.0`

## 能力边界

Stable V1.0 验收 Agent Loop、workspace copy、Docker sandbox、PolicyChain、HITL 基础链路、context assembly、trace、metrics、eval runner 和只读 Web Trace Viewer。

Semantic compaction、Skill Registry 和 Feedback Memory 保留为 experimental。V1.0 不声明真实模型任务成功率、上下文压缩收益或记忆收益。

## 环境

| 项目 | 验收值 |
| --- | --- |
| OS | Windows，Asia/Shanghai |
| Python | 3.12.13 |
| uv | 0.11.7 |
| Docker client | 29.5.2 |
| Docker server | 29.5.2，Docker Desktop |
| Sandbox image | python:3.11-slim，容器内 Python 3.11.15 |

## 验收结果

| 验收项 | 命令或证据 | 结果 |
| --- | --- | --- |
| archive branch | `git show-ref --verify refs/heads/archive/long-run-11-of-60` | PASS，指向 `5d7f163` |
| archive tag | `git rev-parse archive-long-run-11-of-60` | PASS，指向 `5d7f163` |
| stable 分支起点 | `git merge-base --is-ancestor 8f19cd3 stable-v1` | PASS |
| Python 依赖锁 | `uv lock --check` | PASS |
| 完整测试 | `uv run pytest -q` | PASS，70 passed |
| CLI 主入口 | `uv run minicc --help` | PASS |
| run CLI | `uv run minicc run --help` | PASS |
| eval CLI | `uv run minicc eval --help` | PASS |
| web CLI | `uv run minicc web --help` | PASS |
| fake-provider loop | `tests/test_cli.py::test_run_command_fake_provider_writes_complete_evidence_bundle` | PASS，状态为 completed |
| run 证据包 | 同一 fake-provider 测试 | PASS，生成 state、trace、metrics、diff、JSON/Markdown report |
| 普通 eval 状态门 | `tests/test_evals.py::test_eval_runner_rejects_waiting_approval_for_ordinary_case` | PASS |
| HITL 状态门 | `tests/test_evals.py::test_eval_runner_allows_explicit_hitl_waiting_status` | PASS |
| Docker 受限容器 | `docker run` 使用 locked 参数执行 bind-mount probe | PASS，输出 `sandbox-ok` |
| Web Viewer 首页 | 启动 `minicc web` 后请求 `/` | PASS，HTTP 200 |
| Web Viewer runs API | 请求 `/runs` | PASS，HTTP 200，返回 63 条既有 run |
| diff 格式 | `git diff --check` | PASS |
| README 数字声明 | 检索成功率、通过率、准确率和收益数字 | PASS，无未经验证的结果数字 |

Docker smoke 使用与默认 sandbox 一致的关键限制：`--network none`、`--cpus 1`、`--memory 1g`、`--pids-limit 256`、`--cap-drop ALL` 和 `no-new-privileges`。容器成功读取 bind-mounted workspace 文件并在执行后删除。

## 产物约定

每个结束的 run 在 `.minicc/runs/<run_id>/` 下保留：

```text
state.json
trace.jsonl
metrics.json
artifacts/diff.patch
run_report.json
run_report.md
```

Eval suite 另外在 `.minicc/runs/eval_reports/` 生成 `eval_report.json` 和 `eval_report.md`。

## 发布结论

上述验收全部通过后创建 annotated tag `stable-v1.0`。V1.1 才开始对 `C02_fix_failing_test` 进行固定 provider、模型、温度和预算下的 3 次真实模型验收。
