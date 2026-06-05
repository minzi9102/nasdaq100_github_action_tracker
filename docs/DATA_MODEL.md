# 数据模型

## raw 层

保存每个接口的原始结果，便于审计和回放。

```text
data/raw/YYYY-MM-DD/provider_symbol_method.csv
```

## processed 层

保存标准化后的中间结果。

```text
data/processed/YYYY-MM-DD/price_metrics.csv
data/processed/YYYY-MM-DD/price_daily.csv
data/processed/YYYY-MM-DD/macro_daily.csv
data/processed/YYYY-MM-DD/macro_metrics.csv
data/processed/YYYY-MM-DD/qqq_holdings.csv
data/processed/YYYY-MM-DD/breadth_metrics.csv
data/processed/YYYY-MM-DD/data_quality.csv
data/processed/YYYY-MM-DD/fmp_summary.csv
data/processed/YYYY-MM-DD/model_input_metrics.csv
```

## reports 层

保存给人和 ChatGPT 阅读的最终结果。

```text
reports/latest/model_input_metrics.csv
reports/latest/manifest.json
reports/latest/price_daily.csv
reports/latest/price_metrics.csv
reports/latest/macro_daily.csv
reports/latest/macro_metrics.csv
reports/latest/qqq_holdings.csv
reports/latest/breadth_metrics.csv
reports/latest/data_quality.csv
reports/latest/nasdaq100_qqq_daily_tracker.xlsx
reports/archive/YYYY-MM-DD/
```

## state 层

保存最新文件清单。

```text
state/latest_manifest.json
```
