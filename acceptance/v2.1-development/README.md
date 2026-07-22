# V2.1 上下文压缩开发归档

归档入口：本目录仅保留这一份结论。Stable V2.1 未发布；当前结论为
`INCONCLUSIVE`，语义压缩继续保持 experimental，不得对外宣称稳定收益。

## 结论

- A0 第二轮三 case：9/9 PASS，使用 `suite-20260722-v21-round2-a0-final-e96655e`。
- A1 首轮 C02：3/3 PASS，原始 suite 为 `suite-20260721-114932-d19d9a3c`。
- A1 第二轮未形成合格三 case suite：C03 在旧实现上连续耗尽 12-turn 预算；修复后的补跑已停止，未纳入通过率；C07 未形成 A1 正式证据。
- 因路线图要求“两轮、至少三个 case、A1 通过率不低于 A0”，本次不能创建 `stable-v2.1` tag。

## 可追溯证据

机器可读汇总见 [report.json](report.json)，人工结论见 [report.md](report.md)。原始 run 保留在
`.minicc/runs/<run-id>/`，本归档只引用 15 个合格 run；失败和中断尝试不进入 acceptance 目录，
未被 suite 引用的历史 run 已通过 cleanup ledger 清理。

## 当前代码

本归档对应的单个 consolidated 工作区提交已将 V2.1 provider resilience 与 semantic-compaction continuity guard
合并为一个提交；完整回归为 `159 passed`。Provider 超时、重试与压缩摘要连续性修复已进入代码，
但收益证据仍不足以升格为 Stable V2.1。

## 下一步门禁

只需在同一干净提交上完成 A0/A1 各三个 case、各三次，并生成两轮独立 suite；通过后再把本目录
改名为 `acceptance/stable-v2.1/` 并创建稳定 tag。不要复制或手改已有报告来满足门禁。
