# miniCC V3.0 Release Runbook

目标是在新机器完成安装、单 case 运行、四维报告生成和 Viewer 查看。以下命令从仓库根目录执行。

## 1. 安装与前置检查

```powershell
uv sync
docker version
uv run minicc --help
uv run pytest -q tests/test_release_report.py tests/test_server.py
```

真实模型运行前，在 `.env` 配置 `MINICC_BASE_URL`、`MINICC_API_KEY`、`MINICC_MODEL`。密钥不得
写入 Git。首次拉取 Docker 镜像的时间不计入 10 分钟演示窗口。

## 2. 单 case 运行

```powershell
uv run minicc eval eval_cases/capability_suite_v1 `
  --case C02_fix_failing_test `
  --repeat 1
```

命令结束后记录终端打印的 `suite_id`、`suite_manifest` 和 `json_report`。原始 run 位于
`.minicc/runs/<run-id>/`，不可变 suite 位于 `.minicc/suites/<suite-id>/`。

## 3. 固定系统 benchmark

正式矩阵固定为 C01/C02/C03/C04/C09，各 3 次：

```powershell
uv run minicc eval eval_cases/capability_suite_v1 `
  --case C01_repo_onboarding `
  --case C02_fix_failing_test `
  --case C03_add_cli_option `
  --case C04_add_regression_test `
  --case C09_hitl_destructive_command `
  --repeat 3
```

该单命令生成 JSON、Markdown、CSV 和 manifest。正式 V3.0 release gate 会额外锁定干净 Git、
Docker 摘要镜像、canonical case authority 和同一提交。

## 4. 生成四维发布报告

仓库保留 Stable V1.3/V2.0/V2.1/V2.2 的正式归档；本机还需保留 V2.1 报告引用的四个原始
suite。默认入口无需传路径：

```powershell
uv run minicc release-report
```

输出目录为 `.minicc/release-reports/<release-id>/`，固定包含：

```text
report.json
report.md
report.csv
manifest.json
```

报告的每条 claim 必须包含 case、run、配置、source SHA-256、原始 artifact 和复跑命令。任一维
缺失或不可追溯时整体为 FAIL，并拒绝写出伪 PASS 报告。

## 5. 查看报告与原始 run

```powershell
uv run minicc web --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。选择 milestone、formal/development 和 run；Timeline、
Metrics、Diff 中缺失的可选 artifact 会显示空状态，不应导致 Viewer 崩溃。

## 6. 故障定位

- Provider 慢：保持当前进程；默认单请求超时 300 秒，最多重试 2 次，已完成 run 不会被重写。
- Context suite 缺失：从被保留的 `.minicc/suites/<suite-id>/` 恢复，不能伪造 run ID。
- 报告输出目录已存在：换新目录；报告是不可覆盖的。
- 工作区变脏：正式验收前提交有意义的代码变化，不能把 acceptance 临时文件混入 case fixture。
- Viewer artifact 缺失：先检查 version catalog 指针；可选 trace/metrics/diff 缺失应降级显示，
  state 或正式 verifier 缺失则不得计入正式指标。
