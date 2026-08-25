# Real Project Suite v1

这组任务从一个真实项目中的缓存重试、边界测试和 Redis key 使用场景抽取而来，
每个 case 只保留完成任务所需的最小 Java 文件，不复制完整 Spring Boot 仓库、依赖缓存或数据库。

## Cases

- `R01_cache_delete_retry_boundary`: 修复 `CacheDeleteMessage` 的重试计数与延迟时序边界。
- `R02_retry_policy_regression_test`: 为重试策略补边界回归测试，并用 mutation 检查测试确实能发现错误实现。
- `R03_cache_key_builder_feature`: 增加带输入规范化、Locale 无关小写和参数校验的商铺搜索缓存 key。

每个 case 都包含 `initial_verify`。运行器会先确认 fixture 按声明处于失败态；初始验证没有失败时，
Agent 不会启动，结果记为 `infrastructure_error`。Agent 结束后，运行器独立执行最终 `verify.py`，
Agent 的 `final` 文本不能替代该验证。

验证脚本用临时目录调用 `javac/java`，不会把 `.class` 文件写入 fixture。每次运行会保存 trace、
diff、初始/最终验证 JSON、报告和 artifact index，然后按 `cleanup_workspace: true` 删除临时 workspace。

本地确定性检查：

```powershell
python eval_cases/real_project_suite_v1/R01_cache_delete_retry_boundary/fixture/verify.py
python eval_cases/real_project_suite_v1/R02_retry_policy_regression_test/fixture/verify.py
python eval_cases/real_project_suite_v1/R03_cache_key_builder_feature/fixture/verify.py
```

需要调用模型时，在 Provider 配置和本地 Java 17 环境就绪后运行：

```powershell
uv run minicc eval eval_cases/real_project_suite_v1 --case R01_cache_delete_retry_boundary --execute-local --milestone v3.4-development
uv run minicc eval eval_cases/real_project_suite_v1 --repeat 3 --execute-local --milestone v3.4-acceptance
```

正式升格只接受独立 verify 通过的 `passed` verdict；Provider、Java toolchain、setup 或清理问题会单独归为
`infrastructure_error`，不会混入任务通过率。
