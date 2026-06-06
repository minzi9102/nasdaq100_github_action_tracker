# Release Notes

## Current

- 生产日报使用 Alpha Vantage 更新 QQQ compact 日线，并通过统一本地缓存保留 260 行输出。
- Tiingo 作为历史缓存主源，Twelve Data 作为历史修复和生产报价源。
- 市场广度只消费至少 220 行且最新日期不超过 5 个自然日的合格缓存。
- Invesco DNG API 提供 QQQ 全量持仓，并单独输出股票持仓池。
- FMP 已退出生产报价链路，仅保留在独立 provider 能力探测中。
- 新增严格 manifest、数据质量、API 用量、报价失败和缓存质量输出。
- GitHub Actions 拆分为生产日报、能力探测、Tiingo 缓存维护和 Twelve Data 历史修复四个工作流。
- `model_input_metrics.csv` 保持固定客观字段，不输出投资结论。

## v1.0.0

- 建立 Alpha Vantage、FRED、FMP、Tiingo 和 Invesco provider。
- 增加 GitHub Actions 定时运行和 `reports/latest` 输出。
- 增加 provider 扩展说明。
