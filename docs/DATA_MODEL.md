# 数据模型

## raw 层

保存每个接口的原始结果，便于审计和回放。

```text
data/raw/YYYY-MM-DD/provider_symbol_method.csv
```

## processed 层

保存标准化后的中间结果。

```text
data/processed/YYYY-MM-DD/price_summary.csv
data/processed/YYYY-MM-DD/macro_summary.csv
data/processed/YYYY-MM-DD/fmp_summary.csv
data/processed/YYYY-MM-DD/ai_input.csv
```

## reports 层

保存给人和 ChatGPT 阅读的最终结果。

```text
reports/latest/analysis_summary.md
reports/latest/ai_input.csv
reports/latest/nasdaq100_qqq_daily_tracker.xlsx
reports/archive/YYYY-MM-DD/
```

## state 层

保存最新文件清单。

```text
state/latest_manifest.json
```
