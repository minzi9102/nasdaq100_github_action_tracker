# 如何让 ChatGPT 读取仓库并分析

每日 GitHub Actions 运行后，会把最新结果提交到：

```text
reports/latest/
state/latest_manifest.json
```

你可以对 ChatGPT 说：

> 请读取我的 GitHub 仓库链接，重点查看 `reports/latest/model_input_metrics.csv`、`reports/latest/price_metrics.csv`、`reports/latest/macro_daily.csv`、`reports/latest/macro_metrics.csv` 和 `state/latest_manifest.json`，基于客观指标分析今天的纳斯达克100 / QQQ。

建议仓库中长期保留：

- `reports/latest/model_input_metrics.csv`：给模型读取的客观指标表。
- `reports/latest/price_metrics.csv`：QQQ 价格、收益率、波动率、回撤和均线。
- `reports/latest/macro_daily.csv`：FRED 宏观序列最新值。
- `reports/latest/macro_metrics.csv`：宏观衍生指标。
- `reports/latest/nasdaq100_qqq_daily_tracker.xlsx`：完整报表。
- `state/latest_manifest.json`：最新文件索引。

如果 ChatGPT 无法直接访问 GitHub 链接，你也可以把这几个文件上传到对话里。
