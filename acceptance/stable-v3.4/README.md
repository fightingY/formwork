# Stable V3.4 Acceptance

V3.4 验收对象是 `eval_cases/real_project_suite_v1` 的最小真实项目评测闭环。

本次真实模型验收 suite 为 `suite-20260819-191732-986a8475`，执行命令为：

```powershell
uv run minicc eval eval_cases/real_project_suite_v1 --repeat 3 --execute-local --milestone v3.4-acceptance
```

结果为 3 个 case 各 3/3，通过率 `9/9`；9 个 run 均为 `passed`，Provider 错误为 0，清理成功为
9/9。完整 JSON/Markdown/CSV 和不可变 manifest 保存在本目录。

每个 case 使用独立临时 workspace，先验证初始失败，再启动单 Agent；最终是否通过只由预绑定的
`verify.py` 和文件变更断言决定。运行器保存 trace、diff、初始/最终验证输出、verdict 和 artifact
index，随后清理 workspace。Agent 自报完成不构成通过。

本次使用本机 Java 17 和 `--execute-local`，因此归档 stage 是 `development_precheck`，不是要求 Docker
镜像摘要的历史 `release_gate`。本目录只归档精简报告和 manifest，不归档完整 workspace。若未来真实模型
运行受到 Provider、网络或本地 Java 环境影响，应保留 `infrastructure_error` 证据，不将其伪造为任务失败或成功。

报告中的 `source_commit=43cc5a3`、`worktree_dirty=true` 是真实执行时的原始 provenance：当时 V3.4
改动尚未提交。该状态被保留而没有事后改写；本次实现随后以独立提交归档。
