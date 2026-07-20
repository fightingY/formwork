# Stable V2.0.1 正式验收

## 结论

Stable V2.0.1 的 workspace snapshot 证据一致性验收通过。

- C02 真实模型 release gate：`3/3 PASS`。
- Task / Agent / Infrastructure：均为 `3/3 PASS`。
- 完整回归：`132 passed in 90.81s`。
- V2.0 checkpoint/resume 确定性矩阵继续通过。
- 三个正式 run 均具备 workspace manifest、state、trace、metrics 和 diff。
- `stable-v2.0.1` 版本索引包含 3 条记录，dangling entry 为 0。

## 验收对象

| 项目 | 值 |
|---|---|
| 实现提交 | `15713620f67c86dc31b73ac38d0ca969279552e8` |
| Git worktree | clean |
| Provider | `https://api.siliconflow.cn/v1` |
| Model | `deepseek-ai/DeepSeek-V4-Flash` |
| Temperature | `0.0` |
| Sandbox | Docker locked mode |
| Docker image | `python@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0` |
| Case | `C02_fix_failing_test` |
| Repeat | `3` |
| Release gate | `True` |
| Milestone | `stable-v2.0.1` |

正式命令：

```powershell
uv run minicc eval eval_cases `
  --case C02_fix_failing_test `
  --repeat 3 `
  --release-gate `
  --milestone stable-v2.0.1 `
  --output-dir acceptance/stable-v2.0.1
```

## 真实模型结果

| 轮次 | Run | 结果 | Turns | Bash | Duration | Diff |
|---:|---|---|---:|---:|---:|---|
| 1 | `eval-C02_fix_failing_test-r1-20260720-212003-12a3d4d1` | PASS | 4 | 3 | 21,474 ms | `src/calculator.py` |
| 2 | `eval-C02_fix_failing_test-r2-20260720-212029-e9192ba5` | PASS | 7 | 6 | 35,564 ms | `src/calculator.py` |
| 3 | `eval-C02_fix_failing_test-r3-20260720-212110-9e5a4e92` | PASS | 4 | 3 | 32,521 ms | `src/calculator.py` |

三轮 verifier 均通过：

- `python -m unittest discover -s tests` 返回 0；
- diff 未超出 allowlist；
- `src/calculator.py` 不包含禁止模式；
- policy denial、provider error、infrastructure error 均为 0。

## Workspace 证据审计

三个 run 的 `workspace_manifest.json` 均满足：

- `snapshot_mode` 为 `copy`；
- `source_root` 精确指向
  `eval_cases/capability_suite_v1/C02_fix_failing_test/fixture`；
- 初始 workspace 文件数为 4；
- 未读取项目根目录的 `docs/`、`.workbuddy/`、`.minicc/`、`acceptance/` 或历史 run；
- manifest、state、trace、metrics、diff 路径均存在；
- 三轮 diff 内容一致，SHA256 为
  `d328a84f7da953ad6ceb7189335fd873885a888b64d54512bbd950d34e96cf2d`。

Manifest SHA256：

| 轮次 | SHA256 |
|---:|---|
| 1 | `e72726f944dfddf96828551662ac7ddc78f1968e4e747c58a37deca450a1753e` |
| 2 | `f473a8064123f1e32bbd78d50ed1b7da589509fe037a2f665505435f189ff3ae` |
| 3 | `b3a776c2a9a88062953119ca0ea0c4d96fbc8e2b6b6f08c7ce4c9c7729a3e2a8` |

## 确定性回归

```powershell
uv run pytest -q
```

结果：

```text
132 passed in 90.81s (0:01:30)
```

该测试集覆盖 tracked-but-ignored 文件、dirty patch、untracked 文件、ignored allowlist、敏感目录
硬性 deny、非 Git fallback、嵌套 eval fixture 隔离、Agent 自行 commit 后的 baseline diff，以及 V2.0
checkpoint/resume 状态矩阵。

## 归档文件

- `eval_report.json`：机器可读的正式汇总报告；
- `eval_report.md`：人类可读的逐轮结果；
- `README.md`：验收环境、证据审计和归档结论。

V2.0.1 通过后，Stable 主线可以进入 V2.0.2 的 Run / Suite / Report 技术账本开发；不得跳过
V2.0.2 直接进入 V2.1。
