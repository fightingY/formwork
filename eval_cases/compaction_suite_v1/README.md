# V2.1 Context Compaction A/B cases

这组 case 复用 Stable V1 capability fixtures，只改变 context budget，让每个 run 都必须跨越压缩阈值。
`retention_markers` 是确定性的关键事实 oracle；长日志 case 产生的 artifact id 会由 harness 自动加入保留集合。

先用一个 case 各跑三次：

```bash
uv run minicc eval eval_cases/compaction_suite_v1 --case V21_C02_fix_failing_test --repeat 3 --context-variant a0
uv run minicc eval eval_cases/compaction_suite_v1 --case V21_C02_fix_failing_test --repeat 3 --context-variant a1
```

稳定后去掉 `--case` 扩展到三个 case。完成两轮独立 A0/A1 suite 后生成判定报告：

```bash
uv run minicc compaction-report --a0 <round-1-a0-report.json> --a1 <round-1-a1-report.json> \
  --a0 <round-2-a0-report.json> --a1 <round-2-a1-report.json> --output-dir <new-output-dir>
```
