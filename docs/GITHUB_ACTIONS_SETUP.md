# GitHub Actions 部署说明

## 1. 新建仓库

在 GitHub 创建一个新仓库，例如：

```text
nasdaq100-qqq-daily-tracker
```

把本项目所有文件上传到该仓库。

## 2. 添加 Secrets

进入仓库：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

添加：

- `ALPHA_VANTAGE_API_KEY`
- `FRED_API_KEY`
- `FMP_API_KEY`
- `TIINGO_API_TOKEN`

不要把真实密钥写进仓库文件。

## 3. 启用工作流

工作流文件：

```text
.github/workflows/daily-tracker.yml
```

它支持：

- 手动运行：`workflow_dispatch`
- 定时运行：周二到周六 00:00 UTC，即新加坡时间 08:00

## 4. 输出文件

每日运行后会自动提交：

```text
reports/latest/analysis_summary.md
reports/latest/ai_input.csv
reports/latest/nasdaq100_qqq_daily_tracker.xlsx
reports/latest/run_log.csv
state/latest_manifest.json
```

这些文件被提交到仓库后，你可以把仓库链接发给 ChatGPT 读取分析。

## 5. 如果不想把原始数据提交进仓库

编辑 `.github/workflows/daily-tracker.yml`，把：

```bash
git add reports/latest reports/archive data/raw data/processed state/latest_manifest.json
```

改为：

```bash
git add reports/latest state/latest_manifest.json
```

这样只提交最新报告，不提交全部原始数据。
