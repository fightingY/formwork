# Changelog

本文件记录 miniCC 的稳定版本变更。格式参考 Keep a Changelog，版本号遵循语义化版本。

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
