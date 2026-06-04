# 如何让 ChatGPT 读取仓库并分析

每日 GitHub Actions 运行后，会把最新结果提交到：

```text
reports/latest/
state/latest_manifest.json
```

你可以对 ChatGPT 说：

> 请读取我的 GitHub 仓库链接，重点查看 `reports/latest/analysis_summary.md`、`reports/latest/ai_input.csv` 和 `state/latest_manifest.json`，根据项目规则分析今天的纳斯达克100 / QQQ 状态。

建议仓库中长期保留：

- `reports/latest/analysis_summary.md`：最适合直接给 AI 阅读。
- `reports/latest/ai_input.csv`：结构化信号表。
- `reports/latest/nasdaq100_qqq_daily_tracker.xlsx`：完整报表。
- `state/latest_manifest.json`：最新文件索引。

如果 ChatGPT 无法直接访问 GitHub 链接，你也可以把这几个文件上传到对话里。
