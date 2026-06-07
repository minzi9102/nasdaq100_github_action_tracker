# 使用 ChatGPT 分析仓库数据

## 推荐读取顺序

1. `state/latest_manifest.json`：确认报告日期、生成时间和文件清单。
2. `reports/latest/data_quality.csv`：确认 target_date、目标日对齐数量、缓存日期范围、覆盖率和实际 provider。
3. `reports/latest/api_usage.csv`：确认限速、失败和生产启用状态。
4. `reports/latest/model_input_metrics.csv`：读取统一客观指标。
5. `reports/latest/breadth_constituents.csv`：定位未对齐目标日的成分股并核对涨跌口径。
6. 按需读取价格、宏观、持仓、报价和聚合市场广度。

不要把缺失值、低覆盖率或过期数据解释为中性信号。

## 建议长期保留

```text
reports/latest/model_input_metrics.csv
reports/latest/price_daily.csv
reports/latest/price_metrics.csv
reports/latest/macro_daily.csv
reports/latest/macro_metrics.csv
reports/latest/qqq_holdings.csv
reports/latest/qqq_equity_holdings.csv
reports/latest/top_holdings_quotes.csv
reports/latest/quote_failures.csv
reports/latest/breadth_metrics.csv
reports/latest/breadth_constituents.csv
reports/latest/data_quality.csv
reports/latest/api_usage.csv
reports/latest/manifest.json
state/latest_manifest.json
```

## 示例提示词

> 请读取这个仓库的 `state/latest_manifest.json`、`reports/latest/data_quality.csv`、`reports/latest/breadth_constituents.csv`、`reports/latest/api_usage.csv` 和 `reports/latest/model_input_metrics.csv`。先确认报告日期、target_date、目标日对齐率、缓存日期范围、缺失值、实际 provider 和限速情况。只有数据质量足够时，再结合 `price_metrics.csv`、`macro_daily.csv`、`macro_metrics.csv`、`qqq_holdings.csv`、`top_holdings_quotes.csv` 与 `breadth_metrics.csv` 分析 Nasdaq-100 / QQQ。明确区分最近可用广度与严格目标日资格，不把仓库输出当成买卖建议。

如果无法直接访问 GitHub，可以上传上述文件。Excel 适合人工浏览，CSV 和 JSON 更适合模型精确读取。
