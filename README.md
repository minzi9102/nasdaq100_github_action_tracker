# Nasdaq-100 / QQQ Daily Tracker

这是一个运行在 GitHub Actions 上的 Nasdaq-100 / QQQ 客观数据流水线。项目每天采集 QQQ 价格、FRED 宏观序列、Invesco 官方持仓和 Twelve Data 报价，并结合本地历史价格缓存生成 CSV、Excel、质量报告和统一模型输入。

项目只生产客观数据，不生成投资建议、颜色状态、方向判断或买卖动作。

## 当前数据流

1. 使用 Alpha Vantage compact 日线更新 QQQ 本地价格缓存。
2. 当 QQQ 缓存不足 260 行时，依次使用 Tiingo 和 Twelve Data 补齐历史。
3. 从 FRED 获取配置在 `config/fred_series.yml` 中的宏观序列。
4. 从 Invesco 官方 DNG API 获取 QQQ 全量持仓，并筛选股票持仓池。
5. 使用 Twelve Data 获取前 20 大股票持仓报价；失败时可以复用报价缓存。
6. 缓存维护以 QQQ 最新有效收盘日作为 `target_date`，增量补齐完整且新鲜但尚未对齐目标日的成分股。
7. 市场广度读取 `data/cache/prices/` 中合格的历史缓存，并叠加 Twelve Data 最新报价。
8. 生成聚合广度、逐成分股日期与涨跌明细、数据质量、API 用量、模型输入、Excel 和 manifest。

历史缓存必须同时满足以下条件才会进入每日市场广度计算：

- 至少 220 条有效日线记录。
- 最新记录相对运行日期不超过 5 个自然日。

`is_qualified` 只表示缓存完整且不陈旧，不代表已经对齐目标交易日。目标日期默认取 QQQ 最新有效收盘日；缓存维护通过 `is_target_date` 和 `needs_target_update` 区分已对齐与待补齐标的。目标日来源不可用时回退到 `run_date`，并在 manifest 中记录原因。

缓存维护由独立工作流完成，日常市场广度流程不会现场批量请求 Tiingo 历史数据。

## 数据源职责

| 数据源 | 当前用途 | 是否参与生产日报 |
| --- | --- | --- |
| Alpha Vantage | QQQ compact 日线更新 | 是 |
| FRED | 利率、通胀和就业等宏观序列 | 是 |
| Invesco | QQQ 官方全量持仓 | 是，无需 API key |
| Twelve Data | 前 20 大持仓报价、QQQ/成分股历史兜底 | 是 |
| Tiingo | QQQ 和成分股历史缓存主源 | 是 |
| Financial Modeling Prep | 独立 provider 能力探测 | 否，诊断用途 |

各 provider 的注册地址和职责在 `config/sources.yml`，调用预算与节流参数在 `config/api_limits.yml`，流水线开关在 `config/pipeline.yml`。

## 快速开始

### 1. 创建项目虚拟环境

项目要求 Python 3.10 或更高版本，并使用 `uv` 管理项目内 `.venv`：

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

Linux/macOS：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

### 2. 配置密钥

复制 `.env.example` 为 `.env`，并按需要填写：

```env
ALPHA_VANTAGE_API_KEY=
FRED_API_KEY=
TIINGO_API_TOKEN=
TWELVE_DATA_API_KEY=
FMP_API_KEY=
```

其中 `FMP_API_KEY` 只用于独立能力探测。不要提交 `.env` 或在日志、Issue、README、提交记录和报表中暴露真实密钥。

### 3. 运行

Windows：

```powershell
.\.venv\Scripts\python.exe scripts\run_daily.py --as-of auto
```

Linux/macOS：

```bash
.venv/bin/python scripts/run_daily.py --as-of auto
```

也可以传入固定报告日期：

```powershell
.\.venv\Scripts\python.exe scripts\run_daily.py --as-of 2026-06-06
```

辅助命令：

```powershell
# 使用 Tiingo 主源维护历史缓存，必要时由 Twelve Data 兜底
.\.venv\Scripts\python.exe scripts\backfill_price_cache.py --as-of auto

# 使用 Twelve Data 修复高优先级缓存缺口
.\.venv\Scripts\python.exe scripts\repair_twelve_data_history.py --as-of auto

# 探测配置的数据源能力，不改变生产数据源选择
.\.venv\Scripts\python.exe scripts\probe_provider_capabilities.py --as-of auto

# 检查关键日报文件是否存在
.\.venv\Scripts\python.exe scripts\print_repo_status.py
```

## GitHub Actions

仓库当前包含四个工作流：

| 工作流 | 文件 | UTC 计划 |
| --- | --- | --- |
| 每日生产日报 | `.github/workflows/daily-tracker.yml` | 周二至周六 `10:30` |
| Provider 能力探测 | `.github/workflows/provider_capability_probe.yml` | 每天 `09:15` |
| Tiingo 历史缓存维护 | `.github/workflows/tiingo_cache_backfill.yml` | 周一至周五 `03:00`、`05:10`、`07:20` |
| Twelve Data 历史修复 | `.github/workflows/twelve_data_history_repair.yml` | 周一至周五 `08:40` |

所有工作流都支持手动触发。GitHub cron 使用 UTC，换算到本地时间时需要考虑时区。

在仓库 `Settings > Secrets and variables > Actions` 中配置：

- `ALPHA_VANTAGE_API_KEY`
- `FRED_API_KEY`
- `TIINGO_API_TOKEN`
- `TWELVE_DATA_API_KEY`
- `FMP_API_KEY`，仅 provider 能力探测需要
- `MAIL_USERNAME`，生产日报邮件通知的 QQ SMTP 发件邮箱
- `MAIL_PASSWORD`，生产日报邮件通知的 QQ 邮箱 SMTP 授权码，不是邮箱登录密码

生产日报会上传 artifact，把生成的数据、缓存和 manifest 提交回当前分支，并在工作流结束后发送邮件到 `997415931@qq.com`。邮件会附上 `reports/latest/nasdaq100_qqq_daily_tracker.xlsx`；如果日报生成失败导致附件不存在，仍会发送包含运行状态和 Actions 链接的通知。

## 输出

目录职责：

```text
data/raw/YYYY-MM-DD/         原始接口响应的标准化 CSV
data/processed/YYYY-MM-DD/   当次运行的完整处理结果
data/cache/prices/           provider 中立的历史价格缓存
data/cache/quotes/           报价缓存
reports/latest/              最新日报
reports/archive/YYYY-MM-DD/  按日期归档的日报
state/                       各流水线最新 manifest
```

`reports/latest/` 的主要文件：

| 文件 | 内容 |
| --- | --- |
| `model_input_metrics.csv` | 面向外部模型的统一客观指标 |
| `price_daily.csv` / `price_metrics.csv` | QQQ 日线与收益、波动率、均线指标 |
| `macro_daily.csv` / `macro_metrics.csv` | FRED 最新值和衍生宏观指标 |
| `qqq_holdings.csv` | Invesco 官方 QQQ 持仓 |
| `qqq_equity_holdings.csv` | 用于市场广度的股票持仓池 |
| `top_holdings_quotes.csv` | 前 20 大股票持仓报价 |
| `quote_failures.csv` | 报价失败诊断 |
| `breadth_metrics.csv` | 涨跌、均线上方比例和新高新低等市场广度 |
| `breadth_constituents.csv` | 每只成分股的目标日、最近两条有效价格、涨跌方向及 recent/strict 广度资格 |
| `data_quality.csv` | 覆盖率、目标日对齐数量、缓存日期范围、缺失标的和实际 provider |
| `api_usage.csv` | 每个 provider 的调用、额度、限速和端点信息 |
| `run_log.csv` | 当次生产流水线调用日志 |
| `provider_capability_probe.csv` | 独立 provider 能力探测结果 |
| `cache_quality.csv` / `price_cache_api_usage.csv` | 缓存完整性、新鲜度、目标日对齐状态及 Tiingo/Twelve Data 调用记录 |
| `twelve_data_cache_quality.csv` / `twelve_data_history_api_usage.csv` | Twelve Data 修复质量与调用记录 |
| `nasdaq100_qqq_daily_tracker.xlsx` | 汇总核心聚合数据的 Excel 报表；逐成分股广度明细保留在 CSV |
| `manifest.json` | 最新文件、质量摘要、API 用量和校验信息 |

`state/latest_manifest.json` 与 `reports/latest/manifest.json` 保存同一份生产日报清单。辅助工作流分别维护：

- `state/latest_cache_backfill_manifest.json`
- `state/latest_twelve_data_history_repair_manifest.json`
- `state/latest_provider_capability_probe_manifest.json`

## 模型输入契约

`reports/latest/model_input_metrics.csv` 固定使用以下字段：

```text
metric_name,metric_value,metric_date,source,provider,coverage_ratio,is_missing,quality_message
```

使用这些指标分析前，应同时检查：

- `reports/latest/data_quality.csv`
- `reports/latest/api_usage.csv`
- `reports/latest/breadth_constituents.csv`
- `state/latest_manifest.json`

示例提示词：

> 请读取仓库中的 `reports/latest/model_input_metrics.csv`、`reports/latest/data_quality.csv`、`reports/latest/breadth_constituents.csv`、`reports/latest/api_usage.csv` 和 `state/latest_manifest.json`。先检查 target_date、目标日对齐率、缓存日期范围、覆盖率、缺失值和 provider 限速情况，再基于客观指标分析 Nasdaq-100 / QQQ；不要把未对齐或缺失数据解释为中性信号。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试覆盖 provider 规范化、缓存合并与目标日资格判断、历史回退、逐成分股广度明细、客观输出契约、manifest 和数据质量字段。
