# Public Release Checklist

这份清单用于从当前开发仓库准备一个适合放进简历的 GitHub 公开仓库。当前分支保留完整开发证据，不建议直接把当前 HEAD 原样推到 public 仓库。

## 公开仓库保留

- `src/minicc/`：核心运行时代码。
- `tests/`：确定性单元测试和协议/策略/执行链路回归测试。
- `eval_cases/real_project_suite_v1/`：三个不依赖真实仓库的最小验证 fixture；若希望仓库更小，只保留其中一个 case。
- `pyproject.toml`、`uv.lock`、`minicc.example.yaml`、`.env.example`：安装、锁定依赖和配置模板。
- `.github/workflows/ci.yml`、`.gitignore`：公开 CI 和本地产物隔离。
- `README.md`（建议改成 150～250 行的项目说明）、`CHANGELOG.md`（只保留当前稳定版本）、`LICENSE`、`SECURITY.md`。
- 最多保留一份脱敏后的 `acceptance/` 摘要；不要把原始 run artifact 当作源码的一部分。

## 从公开仓库移除

- `minicc.yaml`、`.env`、`.minicc/`、`.venv/`、`dist/`、缓存目录和本地编辑器目录。
- `面试/`、`CLAUDE.md`、`miniClaudeCode.md`、`STABLE_V1_MILESTONE_ROADMAP.md`：这些是内部工作笔记或过长的历史叙事，不属于面向用户的最小文档。
- `acceptance/` 下的历史 JSON/CSV/trace/checkpoint；它们包含本机绝对路径，且体积大、难以复现。保留一份手工整理的指标摘要即可。
- `eval_cases/` 中除选定公开 fixture 外的实验套件、真实项目回放和临时输出。
- `tools/` 中只服务于本地实验的脚本，以及 `scripts/fetch_ci_logs.py`、`scripts/check_ci_run.py` 这类个人 CI 辅助脚本。
- 任何包含真实 provider 名称、内部中转站地址、真实模型账号或真实请求日志的配置/报告。

## 推送前必须做的安全检查

1. 立即轮换当前本机 `minicc.yaml` 中出现过的所有 API key。该文件虽然被 `.gitignore` 忽略，但 Git 历史曾经跟踪过 `minicc.yaml`，所以公开前必须做历史扫描和清理。
2. 从公开分支的完整历史中清除 `minicc.yaml`、`.env`、请求日志和真实 artifact；仅删除工作区文件不能清理 Git 历史。
3. 在干净的公开工作树运行：

   ```powershell
   uv sync --locked --all-groups
   uv run python scripts/public_release_audit.py --history
   uv run ruff check src tests
   uv run mypy src/minicc
   uv run pytest -q
   uv build
   ```

4. 在 GitHub 的 Secret scanning、Dependabot 和 Actions 权限页再次确认没有密钥、绝对路径或私有域名。

## 推荐公开仓库的首页叙事

首页只回答四件事：它解决什么问题、架构边界是什么、如何在无 Provider 的情况下运行确定性测试、如何配置真实 Provider。把“experimental”能力明确标注为实验，不要把历史验收数字写成当前通用成功率。
