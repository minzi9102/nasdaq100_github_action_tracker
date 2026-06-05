# Nasdaq-100 / QQQ Daily Tracker for GitHub Actions

这是一个可部署到 GitHub Actions 的纳斯达克100 / QQQ 每日数据收集项目。

目标：每天自动读取 Alpha Vantage、FRED、Financial Modeling Prep、Tiingo 四类接口，生成结构化数据、Excel 报表和模型输入指标文件，并把最新结果保存到仓库中，方便后续把 GitHub 仓库链接交给 ChatGPT 读取分析。

## 功能

- 读取四个接口：
  - Alpha Vantage：QQQ / 股票日线价格。
  - FRED：美债收益率、利率、通胀等宏观数据。
  - Financial Modeling Prep：股票报价、基本面、关键指标。
  - Tiingo：QQQ / 成分股日线价格备用源。
- GitHub Actions 每日自动运行。
- 使用 GitHub Secrets 保存 API key，不把密钥写入代码。
- 自动输出：
  - `data/raw/YYYY-MM-DD/`：原始接口数据。
  - `data/processed/YYYY-MM-DD/`：标准化数据。
  - `reports/latest/`：最新指标文件和 Excel 数据汇总。
  - `state/latest_manifest.json`：最新输出文件清单。
- 便于扩展：新增数据接口只需要实现一个 provider 类，并在 `config/sources.yml` 中注册。

## 快速开始

### 1. 本地安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows：

```bat
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 本地配置密钥

复制：

```bash
cp .env.example .env
```

在 `.env` 中填写你自己的密钥：

```env
ALPHA_VANTAGE_API_KEY=你的密钥
FRED_API_KEY=你的密钥
FMP_API_KEY=你的密钥
TIINGO_API_TOKEN=你的密钥
```

不要把 `.env` 提交到 GitHub。

### 3. 本地测试

```bash
python scripts/run_daily.py --as-of auto
```

生成结果会出现在：

```text
reports/latest/
data/processed/YYYY-MM-DD/
state/latest_manifest.json
```

### 4. GitHub Actions 部署

把整个项目上传到 GitHub 仓库，然后在仓库设置里添加 Secrets：

- `ALPHA_VANTAGE_API_KEY`
- `FRED_API_KEY`
- `FMP_API_KEY`
- `TIINGO_API_TOKEN`

工作流文件在：

```text
.github/workflows/daily-tracker.yml
```

默认会在新加坡时间周二到周六早上 8:00 自动运行一次，也可以手动点击运行。

## 给 ChatGPT 分析的方式

每日运行后，仓库里会更新：

- `reports/latest/model_input_metrics.csv`
- `reports/latest/price_metrics.csv`
- `reports/latest/macro_daily.csv`
- `reports/latest/macro_metrics.csv`
- `reports/latest/nasdaq100_qqq_daily_tracker.xlsx`
- `state/latest_manifest.json`

以后你可以把 GitHub 仓库链接发给 ChatGPT，并说：

> 请读取这个仓库里的 `reports/latest/model_input_metrics.csv`、`reports/latest/price_metrics.csv`、`reports/latest/macro_daily.csv`、`reports/latest/macro_metrics.csv` 和 `state/latest_manifest.json`，基于客观指标分析今天的纳斯达克100 / QQQ。

如果你启用了 GitHub 连接器或把仓库文件上传给 ChatGPT，我就可以读取并分析这些文件。

## 安全提醒

真实 API key 只放在 `.env` 或 GitHub Secrets 中。不要写进代码、README、issue、提交记录、日志或 Excel 输出文件。
