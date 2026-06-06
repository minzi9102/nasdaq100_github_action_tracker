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
data/processed/YYYY-MM-DD/top_holdings_quotes.csv
data/processed/YYYY-MM-DD/quote_failures.csv
data/processed/YYYY-MM-DD/data_quality.csv
data/processed/YYYY-MM-DD/api_usage.csv
data/processed/YYYY-MM-DD/provider_capability_probe.csv
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
reports/latest/top_holdings_quotes.csv
reports/latest/quote_failures.csv
reports/latest/data_quality.csv
reports/latest/api_usage.csv
reports/latest/provider_capability_probe.csv
reports/latest/nasdaq100_qqq_daily_tracker.xlsx
reports/archive/YYYY-MM-DD/
```

## model_input_metrics 字段

`model_input_metrics.csv` 只汇总客观指标和数据质量，不包含投资建议、颜色状态或方向判断。

```text
metric_name
metric_value
metric_date
source
provider
coverage_ratio
is_missing
quality_message
```

- `source`：指标来自哪个标准化数据集。
- `provider`：实际数据提供方或来源组合。
- `coverage_ratio`：仅覆盖率类指标填写，其他指标留空。
- `quality_message`：单位、计算方法、缺失或限速等客观说明。

## state 层

保存最新文件清单。

```text
state/latest_manifest.json
```

## 价格与报价缓存

```text
data/cache/prices/{symbol}.csv
data/cache/quotes/twelve_data/latest_quotes.csv
```

价格缓存使用统一字段并在每行保留 `source`。旧的 `data/cache/prices/tiingo/` 仅用于兼容读取。

## 诊断字段

- `api_usage.csv`：记录 HTTP/错误分类、实际 endpoint、函数、outputsize、adjusted 和生产启用状态。
- `data_quality.csv`：分别记录历史、报价、前 10、前 20 覆盖率以及 MA200 可用性。
- `top_holdings_quotes.csv`：记录 attempted symbol 以及 API、解析、合并三个阶段状态。
- `quote_failures.csv`：保留每个前 20 标的的详细诊断，最终缺失标的另存轻量原始 JSON。
