# 数据模型

## 数据分层

```text
data/raw/YYYY-MM-DD/         当次接口结果
data/processed/YYYY-MM-DD/   当次标准化结果
data/cache/prices/           provider 中立的历史价格缓存
data/cache/quotes/           Twelve Data 报价缓存
reports/latest/              最新报告
reports/archive/YYYY-MM-DD/  日期归档
state/                       各流水线最新 manifest
```

旧目录 `data/cache/prices/tiingo/` 仅用于兼容读取；新缓存写入 `data/cache/prices/{symbol}.csv`，每行通过 `source` 保留实际来源。

## 生产日报数据集

| 数据集 | 关键字段或职责 |
| --- | --- |
| `price_daily.csv` | `date,symbol,open,high,low,close,adjusted_close,volume,source` |
| `price_metrics.csv` | QQQ 收益率、波动率、回撤、MA50、MA200 |
| `macro_daily.csv` | FRED 序列最新日期和值 |
| `macro_metrics.csv` | 利差、变化率等宏观衍生值 |
| `qqq_holdings.csv` | Invesco 全量持仓及证券类型 |
| `qqq_equity_holdings.csv` | 市场广度使用的股票持仓池 |
| `top_holdings_quotes.csv` | 前 20 大股票持仓报价与三阶段成功状态 |
| `quote_failures.csv` | API、解析、合并和最终缺失诊断 |
| `breadth_metrics.csv` | 涨跌、均线上方比例、新高新低及分母 |
| `data_quality.csv` | 覆盖率、缺失标的、缓存/报价来源和限速状态 |
| `api_usage.csv` | 调用次数、额度、端点、HTTP/错误类型和生产启用状态 |
| `run_log.csv` | provider 调用日志 |
| `model_input_metrics.csv` | 统一客观模型输入 |

上述数据同时写入 `data/processed/YYYY-MM-DD/` 和 `reports/latest/`，能力探测及缓存维护结果由各自工作流补充。

## 缓存资格

市场广度只加载同时满足以下条件的历史缓存：

- 有效价格记录不少于 220 行。
- 最新记录相对运行日期不超过 5 个自然日。

`data_quality.csv` 会记录历史覆盖率、报价覆盖率、前 10/前 20 覆盖率、MA200 可用性、缺失高权重标的和实际 provider。

## 模型输入契约

`model_input_metrics.csv` 固定字段为：

```text
metric_name,metric_value,metric_date,source,provider,coverage_ratio,is_missing,quality_message
```

- `source`：指标来自的标准化数据集。
- `provider`：实际 provider 或来源组合。
- `coverage_ratio`：仅覆盖率类指标填写，其他指标可为空。
- `is_missing`：指标值是否缺失。
- `quality_message`：计算口径、覆盖率、限速或缺失说明。

该文件不包含投资建议、颜色状态、方向判断或买卖动作。

## Manifest

生产日报同时写入：

```text
reports/latest/manifest.json
state/latest_manifest.json
```

内容包括 `as_of`、`generated_at`、`latest_files`、`quality_summary`、`api_usage` 和 `provider_logs`。

辅助 manifest：

```text
state/latest_cache_backfill_manifest.json
state/latest_twelve_data_history_repair_manifest.json
state/latest_provider_capability_probe_manifest.json
```
