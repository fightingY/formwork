# Contributing to miniCC

## 开发环境

需要 Python 3.11 或 3.12、Git 和 uv。涉及真实 Agent 执行时还需要 Docker；工程质量门本身不调用
Provider。

```bash
uv sync --locked --all-groups
```

不要提交 `.env`、`.minicc/`、Provider 凭据、临时 run 或构建产物。

## 提交前检查

```bash
uv run ruff check src tests
uv run mypy src/minicc
uv run pytest --cov=minicc --cov-report=term-missing --cov-report=xml
uv build
git diff --check
```

所有命令必须通过。不要通过降低断言、提高 Agent 预算或删除失败证据来绕过发布门。
覆盖率统计覆盖整个 `minicc` 包，当前硬门为 78%；提高门槛必须依靠新增测试，不得排除 CLI 或
其他低覆盖模块。

## 变更范围

- 一个提交表达一个可解释的行为或治理变化。
- 新能力先标记为 `experimental`，具备确定性测试和独立正式验收后才能升格为 `stable`。
- 不覆盖既有 suite/report，不把失败或中断尝试混入正式 acceptance。
- 修改 Provider、模型、case authority 或缓存布局后，应按对应 runbook 重新运行相关验收。

Pull request 应说明变更动机、验证命令、结果和能力边界；涉及真实 Provider 的验收还应提供可定位的
run/suite ID，但不得提交密钥。
