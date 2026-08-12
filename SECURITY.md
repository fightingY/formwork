# Security Policy

## Supported versions

安全修复仅承诺覆盖最新的 `stable-v*` 标签。旧版 acceptance 证据保持不可变，不会因安全修复而
重写。

## Reporting a vulnerability

请不要在公开 issue 中披露未修复漏洞、凭据或可直接利用的细节。优先使用 GitHub 仓库的
Private vulnerability reporting；如果该入口未启用，请通过仓库所有者的 GitHub 主页私下联系，
只提供建立安全沟通所需的最少信息。

报告应包括受影响版本、复现条件、影响范围和建议的缓解方式。收到报告后，应先确认复现和影响，
再协调修复与披露时间。

## Scope and boundaries

- Docker locked sandbox、PolicyChain 和 HITL 会降低风险，但不是抵御恶意代码的完整隔离证明。
- 不应在 Agent workspace、命令参数、trace、report 或 acceptance 中放置 Provider 密钥。
- 本地执行和显式授权的网络 dev 模式拥有宿主机权限边界内的风险，生产使用者应增加独立机器、
  容器运行时和凭据隔离。
