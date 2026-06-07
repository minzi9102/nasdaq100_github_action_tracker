# Release Notes

## Current

- 生产日报使用 Alpha Vantage 更新 QQQ compact 日线，并通过统一本地缓存保留 260 行输出。
- Tiingo 作为历史缓存主源，Twelve Data 作为历史修复和生产报价源。
- 市场广度只消费至少 220 行且最新日期不超过 5 个自然日的合格缓存。
- 缓存维护使用 QQQ 最新有效收盘日作为目标日期，并增量补齐完整、新鲜但尚未对齐目标日的成分股。
- `cache_quality.csv` 和缓存 manifest 增加目标日期、对齐状态及待补齐数量。
- 新增 `breadth_constituents.csv`，逐股票记录最近两条有效价格、涨跌方向及 recent/strict 广度资格。
- `data_quality.csv` 的广度说明增加目标日对齐数量和缓存最小/最大日期。
- Invesco DNG API 提供 QQQ 全量持仓，并单独输出股票持仓池。
- FMP 已退出生产报价链路，仅保留在独立 provider 能力探测中。
- 新增严格 manifest、数据质量、API 用量、报价失败和缓存质量输出。
- GitHub Actions 拆分为生产日报、能力探测、Tiingo 缓存维护和 Twelve Data 历史修复四个工作流。
- `model_input_metrics.csv` 保持固定客观字段，不输出投资结论。

## v1.0.0

- 建立 Alpha Vantage、FRED、FMP、Tiingo 和 Invesco provider。
- 增加 GitHub Actions 定时运行和 `reports/latest` 输出。
- 增加 provider 扩展说明。
