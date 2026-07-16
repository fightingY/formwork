# Stable V1.3 验收说明

## 验收目标

Stable V1.3 验证工具治理、安全边界和发布证据闭环：

- 模型 action 优先使用供应商原生 JSON mode，并保留严格的唯一 JSON 对象解析兜底。
- Docker 工作区默认只读，仅开放 case 声明的写路径。
- 普通 locked 任务不触发审批；破坏性命令稳定进入 HITL。
- 批准后可以恢复执行，拒绝后可靠终止。
- 报告分别展示任务结果、Agent 终态和基础设施状态。
- C01-C04 固定回归不得低于 Stable V1.2，C09 必须三次稳定进入审批。

## 固定配置

| 项目 | 值 |
| --- | --- |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| JSON mode | 开启；供应商不支持时自动使用本地严格解析 |
| Sandbox | Docker locked，network none |
| Docker image | `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0` |
| Repeat | 每个 case 连续 3 次 |

## 发布门禁

正式验收必须满足：

1. 工作区无未提交修改。
2. 验收代码已经固定到 Git commit。
3. 使用带 SHA256 digest 的 Docker 镜像。
4. 不使用 `--execute-local`。
5. 显式选择完整的 C01/C02/C03/C04/C09 矩阵。
6. 所有 case 在一次完整运行中通过，不拼接历史报告或局部重跑结果。

执行命令：

```powershell
uv run minicc eval eval_cases `
  --case C01_repo_onboarding `
  --case C02_fix_failing_test `
  --case C03_add_cli_option `
  --case C04_add_regression_test `
  --case C09_hitl_destructive_command `
  --repeat 3 `
  --release-gate `
  --output-dir acceptance/stable-v1.3
```

## 证据口径

- 本目录是 Stable V1.3 唯一正式验收入口。
- 开发期间的失败复现和局部重跑统一保存在
  `acceptance/archive/stable-v1.3-development/`，只用于归因，不参与最终通过率计算。
- `eval_report.json` 是机器可读原始汇总，`eval_report.md` 是面向人工审阅的报告。
- 只有完整矩阵通过后，才创建 `stable-v1.3` tag。

## 当前状态

代码级回归已通过，等待本目录生成同一不可变提交上的完整真实模型验收报告。
