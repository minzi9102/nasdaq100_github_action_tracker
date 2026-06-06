# 快速部署清单

## 1. 本地环境

要求 Python 3.10+ 和 `uv`。

Windows：

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

Linux/macOS：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
```

在 `.env` 中填写生产流程需要的密钥：

```text
ALPHA_VANTAGE_API_KEY
FRED_API_KEY
TIINGO_API_TOKEN
TWELVE_DATA_API_KEY
```

`FMP_API_KEY` 仅用于 provider 能力探测。

## 2. 本地运行

```powershell
.\.venv\Scripts\python.exe scripts\run_daily.py --as-of auto
.\.venv\Scripts\python.exe -m pytest
```

缓存和诊断辅助命令见 [README.md](README.md)。

## 3. GitHub Secrets

进入 `Settings > Secrets and variables > Actions`，添加：

```text
ALPHA_VANTAGE_API_KEY
FRED_API_KEY
TIINGO_API_TOKEN
TWELVE_DATA_API_KEY
FMP_API_KEY
```

## 4. GitHub Actions

在 Actions 页面可以手动触发四个工作流：

- `Nasdaq-100 QQQ Daily Tracker`
- `Provider Capability Probe`
- `Tiingo Price Cache Backfill`
- `Twelve Data History Repair`

生产日报计划为周二至周六 `10:30 UTC`。其他缓存维护计划见 [docs/GITHUB_ACTIONS_SETUP.md](docs/GITHUB_ACTIONS_SETUP.md)。

## 5. 检查结果

优先检查：

```text
reports/latest/model_input_metrics.csv
reports/latest/data_quality.csv
reports/latest/api_usage.csv
reports/latest/manifest.json
state/latest_manifest.json
```

不要在未检查数据日期、覆盖率、缓存新鲜度和限速状态时直接分析指标。
