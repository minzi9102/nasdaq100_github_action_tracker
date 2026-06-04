from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd

from qqq_tracker.io import write_csv, write_json, write_markdown
from qqq_tracker.providers import AlphaVantageProvider, FMPProvider, FREDProvider, TiingoProvider
from qqq_tracker.settings import Settings

from .calculations import (
    annualized_vol,
    drawdown_metrics,
    latest_value,
    moving_average,
    pct_change_from_close,
    signal_gte,
    signal_lte,
    signal_price_return,
)
from .report_builder import build_ai_input, build_analysis_summary, build_markdown_summary, write_excel


def as_of_date(value: str) -> str:
    if value == "auto":
        return date.today().isoformat()
    return value


def provider_config(settings: Settings, name: str) -> Dict:
    return settings.sources.get("providers", {}).get(name, {})


def rel_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def make_providers(settings: Settings) -> Dict[str, object]:
    timeout = settings.pipeline.get("api", {}).get("request_timeout_seconds", 30)
    retry_count = settings.pipeline.get("api", {}).get("retry_count", 2)

    av_cfg = provider_config(settings, "alpha_vantage")
    fred_cfg = provider_config(settings, "fred")
    fmp_cfg = provider_config(settings, "fmp")
    tiingo_cfg = provider_config(settings, "tiingo")

    return {
        "alpha_vantage": AlphaVantageProvider(
            settings.get_secret(av_cfg.get("api_key_env", "ALPHA_VANTAGE_API_KEY")),
            av_cfg.get("base_url", "https://www.alphavantage.co/query"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        "fred": FREDProvider(
            settings.get_secret(fred_cfg.get("api_key_env", "FRED_API_KEY")),
            fred_cfg.get("base_url", "https://api.stlouisfed.org/fred"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        "fmp": FMPProvider(
            settings.get_secret(fmp_cfg.get("api_key_env", "FMP_API_KEY")),
            fmp_cfg.get("base_url", "https://financialmodelingprep.com/stable"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        "tiingo": TiingoProvider(
            settings.get_secret(tiingo_cfg.get("api_key_env", "TIINGO_API_TOKEN")),
            tiingo_cfg.get("base_url", "https://api.tiingo.com/tiingo"),
            timeout=timeout,
            retry_count=retry_count,
        ),
    }


def summarize_price(symbol: str, df: pd.DataFrame, source: str, thresholds: Dict) -> Dict:
    if df.empty:
        return {
            "symbol": symbol,
            "source": source,
            "date": None,
            "latest_close": None,
            "return_20d": None,
            "return_60d": None,
            "vol_20d": None,
            "current_drawdown": None,
            "max_drawdown": None,
            "ma_50": None,
            "ma_200": None,
            "signal_return_20d": "灰色",
            "signal_return_60d": "灰色",
            "signal_vol_20d": "灰色",
            "signal_drawdown": "灰色",
        }
    d = df.sort_values("date").copy()
    price_col = "adjusted_close" if "adjusted_close" in d.columns else "adjClose" if "adjClose" in d.columns else "close"
    latest = d.iloc[-1]
    dd = drawdown_metrics(d, price_col=price_col)
    r20 = pct_change_from_close(d, 20, price_col=price_col)
    r60 = pct_change_from_close(d, 60, price_col=price_col)
    v20 = annualized_vol(d, 20, price_col=price_col)
    price_thresholds = thresholds.get("price", {})
    return {
        "symbol": symbol,
        "source": source,
        "date": latest.get("date"),
        "latest_close": latest.get(price_col),
        "return_20d": r20,
        "return_60d": r60,
        "vol_20d": v20,
        "current_drawdown": dd.get("current_drawdown"),
        "max_drawdown": dd.get("max_drawdown"),
        "ma_50": moving_average(d, 50, price_col=price_col),
        "ma_200": moving_average(d, 200, price_col=price_col),
        "signal_return_20d": signal_price_return(r20, **price_thresholds.get("qqq_20d_return", {"green_gte": 0, "yellow_gte": -0.05})),
        "signal_return_60d": signal_price_return(r60, **price_thresholds.get("qqq_60d_return", {"green_gte": 0, "yellow_gte": -0.10})),
        "signal_vol_20d": signal_lte(v20, **price_thresholds.get("qqq_20d_vol", {"green_lte": 0.25, "yellow_lte": 0.35})),
        "signal_drawdown": signal_gte(dd.get("current_drawdown"), **price_thresholds.get("current_drawdown", {"green_gte": -0.10, "yellow_gte": -0.15})),
    }


def fred_latest(df: pd.DataFrame) -> tuple[float | None, str | None]:
    if df.empty or "value" not in df.columns:
        return None, None
    d = df.dropna(subset=["value"]).sort_values("date")
    if d.empty:
        return None, None
    latest = d.iloc[-1]
    return float(latest["value"]), latest.get("date")


def fred_prior_value(df: pd.DataFrame, periods_back: int) -> float | None:
    if df.empty or "value" not in df.columns:
        return None
    d = df.dropna(subset=["value"]).sort_values("date")
    if len(d) <= periods_back:
        return None
    return float(d.iloc[-periods_back - 1]["value"])


def fred_recent_pct_changes(df: pd.DataFrame, periods: int = 3) -> list[float]:
    if df.empty or "value" not in df.columns:
        return []
    d = df.dropna(subset=["value"]).sort_values("date")
    values = d["value"].astype(float)
    return [float(x) for x in values.pct_change().dropna().tail(periods)]


def macro_signal_yield_level(value: float | None) -> str:
    if value is None:
        return "灰色"
    if value >= 5.0:
        return "红色"
    if value >= 4.25:
        return "黄色"
    return "绿色"


def macro_signal_rising_change(value: float | None, yellow_gte: float, red_gte: float) -> str:
    if value is None:
        return "灰色"
    if value >= red_gte:
        return "红色"
    if value >= yellow_gte:
        return "黄色"
    return "绿色"


def macro_signal_yield_spread(spread: float | None, one_month_change: float | None) -> str:
    if spread is None:
        return "灰色"
    if spread <= -0.50 or (one_month_change is not None and spread < 0 and one_month_change <= -0.25):
        return "红色"
    if spread < 0 or (one_month_change is not None and one_month_change <= -0.10):
        return "黄色"
    return "绿色"


def macro_signal_three_changes(changes: list[float]) -> str:
    if len(changes) < 3:
        return "灰色"
    increasing = changes[0] < changes[1] < changes[2]
    if increasing and changes[2] > 0:
        return "红色"
    if changes[1] < changes[2] and changes[2] > 0:
        return "黄色"
    return "绿色"


def build_macro_signal_rows(fred_frames: Dict[str, pd.DataFrame]) -> list[Dict]:
    rows: list[Dict] = []
    dgs10_value, dgs10_date = fred_latest(fred_frames.get("DGS10", pd.DataFrame()))
    dgs10_prior = fred_prior_value(fred_frames.get("DGS10", pd.DataFrame()), 21)
    dgs10_change = dgs10_value - dgs10_prior if dgs10_value is not None and dgs10_prior is not None else None
    dgs2_value, dgs2_date = fred_latest(fred_frames.get("DGS2", pd.DataFrame()))
    dgs2_prior = fred_prior_value(fred_frames.get("DGS2", pd.DataFrame()), 21)
    spread = dgs10_value - dgs2_value if dgs10_value is not None and dgs2_value is not None else None
    prior_spread = dgs10_prior - dgs2_prior if dgs10_prior is not None and dgs2_prior is not None else None
    spread_change = spread - prior_spread if spread is not None and prior_spread is not None else None

    rows.extend([
        {
            "series_id": "DGS10_LEVEL_SIGNAL",
            "name": "美国10年期国债收益率水平",
            "latest_date": dgs10_date,
            "latest_value": dgs10_value,
            "status": macro_signal_yield_level(dgs10_value),
            "threshold": "<4.25%绿色，4.25%-5%黄色，>=5%红色",
            "direction": "长期利率压力",
        },
        {
            "series_id": "DGS10_1M_CHANGE_SIGNAL",
            "name": "美国10年期国债收益率1月变化",
            "latest_date": dgs10_date,
            "latest_value": dgs10_change,
            "status": macro_signal_rising_change(dgs10_change, 0.15, 0.30),
            "threshold": "<0.15个百分点绿色，0.15-0.30黄色，>=0.30红色",
            "direction": "长期利率上行速度",
        },
        {
            "series_id": "DGS10_DGS2_SPREAD_SIGNAL",
            "name": "2年/10年利差",
            "latest_date": dgs10_date or dgs2_date,
            "latest_value": spread,
            "status": macro_signal_yield_spread(spread, spread_change),
            "threshold": "正利差绿色，倒挂黄色，深度倒挂或倒挂加深红色",
            "direction": "收益率曲线压力",
        },
    ])

    for series_id, label in [("CPIAUCSL", "CPI近3次变化"), ("PCEPI", "PCE近3次变化")]:
        value, latest_date = fred_latest(fred_frames.get(series_id, pd.DataFrame()))
        changes = fred_recent_pct_changes(fred_frames.get(series_id, pd.DataFrame()), 3)
        rows.append({
            "series_id": f"{series_id}_3_CHANGE_SIGNAL",
            "name": label,
            "latest_date": latest_date,
            "latest_value": changes[-1] if changes else None,
            "status": macro_signal_three_changes(changes),
            "threshold": "近3次环比变化连续上行且最新为正红色，最新上行黄色，否则绿色",
            "direction": f"通胀压力，最新指数={value}" if value is not None else "通胀压力",
        })

    unrate_value, unrate_date = fred_latest(fred_frames.get("UNRATE", pd.DataFrame()))
    unrate_prior = fred_prior_value(fred_frames.get("UNRATE", pd.DataFrame()), 3)
    unrate_change = unrate_value - unrate_prior if unrate_value is not None and unrate_prior is not None else None
    rows.append({
        "series_id": "UNRATE_3M_CHANGE_SIGNAL",
        "name": "失业率3个月变化",
        "latest_date": unrate_date,
        "latest_value": unrate_change,
        "status": macro_signal_rising_change(unrate_change, 0.30, 0.50),
        "threshold": "<0.30个百分点绿色，0.30-0.50黄色，>=0.50红色",
        "direction": f"就业降温速度，最新失业率={unrate_value}" if unrate_value is not None else "就业降温速度",
    })
    return rows


def run_daily(as_of: str = "auto") -> Dict:
    settings = Settings()
    settings.ensure_dirs()
    run_date = as_of_date(as_of)
    providers = make_providers(settings)

    raw_dir = settings.paths.raw_dir / run_date
    processed_dir = settings.paths.processed_dir / run_date
    archive_dir = settings.paths.reports_archive_dir / run_date
    latest_dir = settings.paths.reports_latest_dir
    for p in [raw_dir, processed_dir, archive_dir, latest_dir]:
        p.mkdir(parents=True, exist_ok=True)

    logs: List[Dict] = []
    price_frames = []
    price_summaries = []

    # Alpha Vantage price source
    if settings.pipeline.get("run", {}).get("fetch_alpha_vantage_prices", True):
        av: AlphaVantageProvider = providers["alpha_vantage"]  # type: ignore[assignment]
        for symbol in settings.symbols.get("price_symbols", ["QQQ"]):
            result = av.daily_adjusted(symbol, outputsize=provider_config(settings, "alpha_vantage").get("default_outputsize", "compact"))
            logs.append({"provider": result.name, "method": "daily_adjusted", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok:
                df = result.data
                write_csv(df, raw_dir / f"alpha_vantage_{symbol}_daily.csv")
                price_frames.append(df)
                price_summaries.append(summarize_price(symbol, df, "alpha_vantage", settings.thresholds))

    # Tiingo fallback / cross-check price source
    if settings.pipeline.get("run", {}).get("fetch_tiingo_prices", True):
        tiingo: TiingoProvider = providers["tiingo"]  # type: ignore[assignment]
        end = run_date if run_date != "auto" else date.today().isoformat()
        start = (datetime.fromisoformat(end) - timedelta(days=420)).date().isoformat()
        for symbol in settings.symbols.get("price_symbols", ["QQQ"]):
            result = tiingo.daily_prices(symbol, start_date=start, end_date=end)
            logs.append({"provider": result.name, "method": "daily_prices", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok:
                df = result.data
                write_csv(df, raw_dir / f"tiingo_{symbol}_daily.csv")
                # Only use as summary if Alpha Vantage did not return this symbol.
                if symbol not in [x.get("symbol") for x in price_summaries]:
                    price_frames.append(df)
                    price_summaries.append(summarize_price(symbol, df, "tiingo", settings.thresholds))

    price_summary = pd.DataFrame(price_summaries)
    write_csv(price_summary, processed_dir / "price_summary.csv")

    # FRED macro
    macro_rows = []
    fred_frames: Dict[str, pd.DataFrame] = {}
    fred: FREDProvider = providers["fred"]  # type: ignore[assignment]
    if settings.pipeline.get("run", {}).get("fetch_fred_macro", True):
        series_map = settings.fred_series.get("series", {})
        for series_id, name in series_map.items():
            result = fred.observations(series_id, limit=365)
            logs.append({"provider": result.name, "method": "observations", "symbol": series_id, "ok": result.ok, "message": result.message})
            if result.ok:
                write_csv(result.data, raw_dir / f"fred_{series_id}.csv")
                fred_frames[series_id] = result.data
                latest = latest_value(result.data)
                last_date = result.data.sort_values("date").iloc[-1]["date"] if not result.data.empty else None
                macro_rows.append({
                    "series_id": series_id,
                    "name": name,
                    "latest_date": last_date,
                    "latest_value": latest,
                    "status": "信息" if latest is not None else "灰色",
                    "threshold": "原始FRED观测值，衍生信号见下方",
                    "direction": "宏观原始数据",
                })
        macro_rows.extend(build_macro_signal_rows(fred_frames))
    macro_summary = pd.DataFrame(macro_rows)
    write_csv(macro_summary, processed_dir / "macro_summary.csv")

    # FMP quotes and key metrics
    fmp: FMPProvider = providers["fmp"]  # type: ignore[assignment]
    fmp_rows = []
    key_metric_frames = []
    if settings.pipeline.get("run", {}).get("fetch_fmp_quotes", True):
        for symbol in settings.symbols.get("fundamental_symbols", []):
            result = fmp.quote(symbol)
            logs.append({"provider": result.name, "method": "quote", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok:
                write_csv(result.data, raw_dir / f"fmp_{symbol}_quote.csv")
            fmp_rows.append({"symbol": symbol, "ok": result.ok, "rows": len(result.data), "message": result.message})

    if settings.pipeline.get("run", {}).get("fetch_fmp_key_metrics", True):
        for symbol in settings.symbols.get("fundamental_symbols", [])[:5]:
            result = fmp.key_metrics(symbol, limit=5)
            logs.append({"provider": result.name, "method": "key_metrics", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok:
                df = result.data
                write_csv(df, raw_dir / f"fmp_{symbol}_key_metrics.csv")
                key_metric_frames.append(df)
    fmp_summary = pd.DataFrame(fmp_rows)
    write_csv(fmp_summary, processed_dir / "fmp_summary.csv")
    key_metrics = pd.concat(key_metric_frames, ignore_index=True) if key_metric_frames else pd.DataFrame()
    write_csv(key_metrics, processed_dir / "fmp_key_metrics.csv")

    logs_df = pd.DataFrame(logs)
    write_csv(logs_df, processed_dir / "run_log.csv")

    ai_input = build_ai_input(price_summary, macro_summary, fmp_summary)
    analysis = build_analysis_summary(ai_input)
    write_csv(ai_input, processed_dir / "ai_input.csv")
    write_csv(analysis, processed_dir / "analysis_summary.csv")
    write_csv(ai_input, latest_dir / "ai_input.csv")
    write_csv(analysis, latest_dir / "analysis_summary.csv")
    write_csv(price_summary, latest_dir / "price_summary.csv")
    write_csv(macro_summary, latest_dir / "macro_summary.csv")
    write_csv(fmp_summary, latest_dir / "fmp_summary.csv")
    write_csv(logs_df, latest_dir / "run_log.csv")

    xlsx_path = latest_dir / "nasdaq100_qqq_daily_tracker.xlsx"
    write_excel(xlsx_path, {
        "价格摘要": price_summary,
        "宏观摘要": macro_summary,
        "FMP可用性": fmp_summary,
        "FMP关键指标": key_metrics,
        "AI输入层": ai_input,
        "分析判断层": analysis,
        "运行日志": logs_df,
    })

    archive_xlsx_path = archive_dir / f"nasdaq100_qqq_daily_tracker_{run_date}.xlsx"
    shutil.copy2(xlsx_path, archive_xlsx_path)
    md_text = build_markdown_summary(run_date, ai_input, analysis, "state/latest_manifest.json")
    write_markdown(md_text, latest_dir / "analysis_summary.md")
    write_markdown(md_text, archive_dir / f"analysis_summary_{run_date}.md")

    manifest = {
        "as_of": run_date,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "latest_files": {
            "markdown_summary": rel_path(latest_dir / "analysis_summary.md", settings.paths.root),
            "excel_report": rel_path(xlsx_path, settings.paths.root),
            "ai_input_csv": rel_path(latest_dir / "ai_input.csv", settings.paths.root),
            "price_summary_csv": rel_path(latest_dir / "price_summary.csv", settings.paths.root),
            "macro_summary_csv": rel_path(latest_dir / "macro_summary.csv", settings.paths.root),
            "fmp_summary_csv": rel_path(latest_dir / "fmp_summary.csv", settings.paths.root),
            "run_log_csv": rel_path(latest_dir / "run_log.csv", settings.paths.root),
        },
        "provider_logs": logs,
    }
    write_json(manifest, settings.paths.state_dir / "latest_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
