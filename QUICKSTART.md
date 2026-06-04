# 快速部署清单

## 一、本地测试

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 在 .env 中填写四个密钥
python scripts/run_daily.py --as-of auto
```

## 二、GitHub Secrets

仓库中添加四个 Repository Secrets：

```text
ALPHA_VANTAGE_API_KEY
FRED_API_KEY
FMP_API_KEY
TIINGO_API_TOKEN
```

## 三、手动运行 GitHub Actions

进入 GitHub 仓库：

```text
Actions -> Nasdaq-100 QQQ Daily Tracker -> Run workflow
```

## 四、每日自动运行

默认工作流：

```text
.github/workflows/daily-tracker.yml
```

默认时间：新加坡时间周二到周六早上 8:00。

## 五、给 ChatGPT 分析

把仓库链接发给 ChatGPT，并指定读取：

```text
reports/latest/analysis_summary.md
reports/latest/ai_input.csv
state/latest_manifest.json
```
