请读取这个仓库中的以下文件：

- reports/latest/model_input_metrics.csv
- reports/latest/price_daily.csv
- reports/latest/price_metrics.csv
- reports/latest/macro_daily.csv
- reports/latest/macro_metrics.csv
- reports/latest/qqq_holdings.csv
- reports/latest/breadth_metrics.csv
- reports/latest/data_quality.csv
- reports/latest/manifest.json

其中 `model_input_metrics.csv` 的字段为：

```text
metric_name, metric_value, metric_date, source, provider, coverage_ratio, is_missing, quality_message
```

请根据这些文件，基于客观指标生成今天的纳斯达克100 / QQQ 分析，包括：

1. 数据质量是否足够。
2. 价格指标有哪些变化。
3. 宏观指标有哪些变化。
4. QQQ 持仓集中度和市场广度数据是否可用。
5. 基本面数据是否可用。
6. 哪些数据需要补充或重新抓取。

不要把仓库输出文件中的指标当成买卖建议；需要投资判断时请单独说明推理依据。
