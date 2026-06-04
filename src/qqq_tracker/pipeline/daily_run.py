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
            result = av.daily_adjusted(symbol, outputsize=provider_config(settings, "alpha_vantage").get("default_outputsize", "full"))
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
    fred: FREDProvider = providers["fred"]  # type: ignore[assignment]
    if settings.pipeline.get("run", {}).get("fetch_fred_macro", True):
        series_map = settings.fred_series.get("series", {})
        for series_id, name in series_map.items():
            result = fred.observations(series_id, limit=365)
            logs.append({"provider": result.name, "method": "observations", "symbol": series_id, "ok": result.ok, "message": result.message})
            if result.ok:
                write_csv(result.data, raw_dir / f"fred_{series_id}.csv")
                latest = latest_value(result.data)
                last_date = result.data.sort_values("date").iloc[-1]["date"] if not result.data.empty else None
                macro_rows.append({"series_id": series_id, "name": name, "latest_date": last_date, "latest_value": latest, "status": "信息" if latest is not None else "灰色"})
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
            "markdown_summary": str(latest_dir / "analysis_summary.md"),
            "excel_report": str(xlsx_path),
            "ai_input_csv": str(latest_dir / "ai_input.csv"),
            "price_summary_csv": str(latest_dir / "price_summary.csv"),
            "macro_summary_csv": str(latest_dir / "macro_summary.csv"),
            "fmp_summary_csv": str(latest_dir / "fmp_summary.csv"),
            "run_log_csv": str(latest_dir / "run_log.csv"),
        },
        "provider_logs": logs,
    }
    write_json(manifest, settings.paths.state_dir / "latest_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
