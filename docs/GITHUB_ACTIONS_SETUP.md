# GitHub Actions 部署说明

## 1. Secrets

进入：

```text
Settings > Secrets and variables > Actions > New repository secret
```

生产日报与缓存维护需要：

- `ALPHA_VANTAGE_API_KEY`
- `FRED_API_KEY`
- `TIINGO_API_TOKEN`
- `TWELVE_DATA_API_KEY`

生产日报邮件通知额外需要：

- `MAIL_USERNAME`：QQ SMTP 发件邮箱
- `MAIL_PASSWORD`：QQ 邮箱 SMTP 授权码，不是邮箱登录密码

能力探测额外使用：

- `FMP_API_KEY`

Invesco 官方持仓接口不需要 API key。

## 2. 工作流

所有工作流都支持 `workflow_dispatch` 手动触发。生产日报和 Tiingo 缓存回填由华为云 FunctionGraph 定时触发 GitHub `workflow_dispatch`；Provider 探测和 Twelve Data 修复仍使用 GitHub cron。

| 工作流 | 文件 | 计划 | 主要职责 |
| --- | --- | --- | --- |
| Nasdaq-100 QQQ Daily Tracker | `.github/workflows/daily-tracker.yml` | 华为云：北京时间周二至周六 `18:30` | 生成生产日报并发送邮件通知 |
| Provider Capability Probe | `.github/workflows/provider_capability_probe.yml` | GitHub cron：每天 `09:15 UTC` | 探测 Alpha Vantage、FMP、Twelve Data |
| Tiingo Price Cache Backfill | `.github/workflows/tiingo_cache_backfill.yml` | 华为云：北京时间周一至周五 `11:00`、`13:10`、`15:20` | 维护历史缓存，Twelve Data 可兜底 |
| Twelve Data History Repair | `.github/workflows/twelve_data_history_repair.yml` | GitHub cron：周一至周五 `08:40 UTC` | 修复高优先级缓存缺口 |

华为云 FunctionGraph 的函数名称、环境变量、定时触发器和函数代码见 `docs/HUAWEI_FUNCTIONGRAPH_SCHEDULES.md`。

## 3. 权限与提交

工作流使用：

```yaml
permissions:
  contents: write
```

运行完成后，GitHub Actions 会将对应报告、缓存和 state manifest 提交回触发分支。若启用了分支保护，需要允许 GitHub Actions 写入，或改为通过 PR 更新生成数据。

## 4. Artifact 与仓库输出

生产日报 artifact 包含：

```text
reports/latest/
data/processed/
data/cache/prices/
data/cache/quotes/
state/latest_manifest.json
```

生产日报工作流结束后会通过 QQ SMTP 发送邮件到 `997415931@qq.com`。成功生成 Excel 时会附加 `reports/latest/nasdaq100_qqq_daily_tracker.xlsx`；如果前置步骤失败或附件不存在，邮件正文仍会包含运行状态、分支、commit 和 Actions 链接。

Tiingo 缓存维护 artifact 还包含：

```text
reports/latest/cache_quality.csv
reports/latest/price_cache_api_usage.csv
state/latest_cache_backfill_manifest.json
```

其中缓存质量输出包含 QQQ `target_date`、各成分股目标日对齐状态和仍待补齐数量。生产日报的 `reports/latest/` 还包含 `breadth_constituents.csv`，用于审计逐股票 recent/strict 广度资格。

各工作流提交的准确路径以对应 YAML 中的 `git add` 和 artifact `path` 为准。

## 5. 减少仓库存档

如果不希望提交 `data/raw/`、`data/processed/` 或归档 Excel，应同时修改：

1. 对应工作流的 `git add` 路径。
2. artifact 路径。
3. 下游读取逻辑和文档。

不要只改 `.gitignore`，因为工作流对缓存使用了 `git add -f`。
