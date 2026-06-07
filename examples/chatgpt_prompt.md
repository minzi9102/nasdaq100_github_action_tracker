请按以下顺序读取仓库文件：

1. `state/latest_manifest.json`
2. `reports/latest/data_quality.csv`
3. `reports/latest/api_usage.csv`
4. `reports/latest/model_input_metrics.csv`
5. `reports/latest/price_metrics.csv`
6. `reports/latest/macro_daily.csv`
7. `reports/latest/macro_metrics.csv`
8. `reports/latest/qqq_holdings.csv`
9. `reports/latest/top_holdings_quotes.csv`
10. `reports/latest/breadth_metrics.csv`
11. `reports/latest/breadth_constituents.csv`

`model_input_metrics.csv` 的固定字段为：

```text
metric_name,metric_value,metric_date,source,provider,coverage_ratio,is_missing,quality_message
```

请完成：

1. 确认报告日期、生成时间以及关键文件是否存在。
2. 检查 target_date、目标日对齐数量、缓存日期范围、历史和报价覆盖率、缺失高权重标的及 provider 限速。
3. 使用 `breadth_constituents.csv` 区分 recent breadth 与 strict target-date eligibility，再说明市场广度的客观变化。
4. 对缺失、过期或低覆盖率数据明确降级结论，不将其视为中性。
5. 区分原始事实、仓库计算指标和你的推断。
6. 不把仓库输出直接解释为投资建议或买卖动作。
