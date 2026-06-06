from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import AlphaVantageProvider, FMPProvider, FREDProvider, InvescoProvider, TiingoProvider, TwelveDataProvider
from qqq_tracker.settings import Settings

from .calculations import (
    annualized_vol,
    drawdown_metrics,
    latest_value,
    moving_average,
    pct_change_from_close,
)
from .report_builder import build_model_input_metrics_v2, write_excel

PRICE_DAILY_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "adjusted_close", "volume", "source"]
PRICE_METRICS_COLUMNS = [
    "symbol",
    "source",
    "date",
    "latest_close",
    "return_20d",
    "return_60d",
    "vol_20d",
    "current_drawdown",
    "max_drawdown",
    "ma_50",
    "ma_200",
]
MACRO_DAILY_COLUMNS = ["series_id", "name", "latest_date", "latest_value", "source"]
MACRO_METRICS_COLUMNS = ["metric_name", "metric_value", "unit_or_method", "data_date", "source"]
QQQ_HOLDINGS_COLUMNS = [
    "date",
    "symbol",
    "company_name",
    "weight",
    "sector",
    "security_type_code",
    "security_type_name",
    "source",
]
QQQ_EQUITY_HOLDINGS_COLUMNS = QQQ_HOLDINGS_COLUMNS.copy()
BREADTH_METRICS_COLUMNS = ["metric_name", "metric_value", "denominator", "data_date", "source", "is_missing"]
TOP_HOLDINGS_QUOTES_COLUMNS = [
    "symbol",
    "company_name",
    "weight",
    "price",
    "change",
    "changes_percentage",
    "market_cap",
    "pe",
    "eps",
    "provider",
    "quote_time",
    "is_missing",
    "error_type",
    "attempted_symbol",
    "was_api_success",
    "was_parse_success",
    "was_merge_success",
]
QUOTE_FAILURE_COLUMNS = [
    "symbol",
    "company_name",
    "weight",
    "provider",
    "endpoint",
    "attempted_symbol",
    "http_status",
    "api_status",
    "raw_error",
    "parse_error",
    "was_api_success",
    "was_parse_success",
    "was_merge_success",
    "final_missing",
]
QUOTE_CACHE_COLUMNS = [
    "symbol",
    "price",
    "change",
    "changes_percentage",
    "volume",
    "datetime",
    "provider",
    "fetched_at",
]
DATA_QUALITY_COLUMNS = [
    "dataset",
    "provider",
    "ok",
    "rows",
    "symbol_coverage_ratio",
    "weight_coverage_ratio",
    "missing_symbols",
    "missing_top_weight_symbols",
    "rate_limited",
    "stopped_after_429",
    "remaining_symbols_skipped",
    "cache_rows_used",
    "live_rows_fetched",
    "fallback_provider",
    "history_coverage_ratio",
    "quote_coverage_ratio",
    "top10_quote_coverage_ratio",
    "top20_quote_coverage_ratio",
    "ma200_available",
    "actual_quote_provider",
    "actual_history_provider",
    "message",
]
API_USAGE_COLUMNS = [
    "run_date",
    "provider",
    "endpoint",
    "calls_attempted",
    "calls_success",
    "calls_failed",
    "rate_limited",
    "limit_window",
    "limit_value",
    "credits_used",
    "symbols_requested",
    "symbols_loaded",
    "stopped_after_429",
    "retry_after_seconds",
    "http_status",
    "error_type",
    "function",
    "outputsize",
    "adjusted",
    "actual_endpoint",
    "production_enabled",
    "premium_blocked",
    "message",
]
BREATH_EXCLUDE_SYMBOLS = {"USD"}
FULL_HISTORY_DAYS = 420
INCREMENTAL_LOOKBACK_DAYS = 10
MIN_HISTORY_ROWS = 220
QQQ_OUTPUT_ROWS = 260
MAX_BACKFILL_SYMBOLS_PER_RUN = 15
TOP_WEIGHT_MISSING_LIMIT = 10


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
    twelve_cfg = provider_config(settings, "twelve_data")
    invesco_cfg = provider_config(settings, "invesco")

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
        "twelve_data": TwelveDataProvider(
            settings.get_secret(twelve_cfg.get("api_key_env", "TWELVE_DATA_API_KEY")),
            twelve_cfg.get("base_url", "https://api.twelvedata.com"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        "invesco": InvescoProvider(
            invesco_cfg.get("holdings_url"),
            timeout=timeout,
            retry_count=retry_count,
        ),
    }


def normalize_price_daily(df: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    out = pd.DataFrame(columns=PRICE_DAILY_COLUMNS)
    if df.empty:
        return out
    d = df.copy()
    out["date"] = pd.to_datetime(d.get("date"), errors="coerce").dt.date.astype(str)
    out["symbol"] = d.get("symbol", symbol)
    out["open"] = d.get("open")
    out["high"] = d.get("high")
    out["low"] = d.get("low")
    out["close"] = d.get("close")
    if "adjusted_close" in d.columns:
        out["adjusted_close"] = d["adjusted_close"]
    elif "adjClose" in d.columns:
        out["adjusted_close"] = d["adjClose"]
    else:
        out["adjusted_close"] = d.get("close")
    out["volume"] = d.get("volume")
    out["source"] = d["source"] if "source" in d.columns else source
    return out.dropna(subset=["date"]).sort_values(["symbol", "source", "date"]).reset_index(drop=True)


def choose_metric_frame(frames: list[tuple[str, pd.DataFrame]]) -> tuple[str, pd.DataFrame]:
    usable = [(source, df) for source, df in frames if not df.empty]
    if not usable:
        return "", pd.DataFrame()
    long_history = [(source, df) for source, df in usable if len(df.dropna(subset=["date"])) >= 200]
    candidates = long_history or usable
    for source, df in candidates:
        if source == "tiingo":
            return source, df
    return candidates[0]


def summarize_price(symbol: str, df: pd.DataFrame, source: str) -> Dict:
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
        }
    d = df.sort_values("date").copy()
    price_col = "adjusted_close" if "adjusted_close" in d.columns else "adjClose" if "adjClose" in d.columns else "close"
    latest = d.iloc[-1]
    dd = drawdown_metrics(d, price_col=price_col)
    r20 = pct_change_from_close(d, 20, price_col=price_col)
    r60 = pct_change_from_close(d, 60, price_col=price_col)
    v20 = annualized_vol(d, 20, price_col=price_col)
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
    }


def quality_row(
    dataset: str,
    provider: str,
    ok: bool,
    rows: int,
    symbol_coverage_ratio: float | None = None,
    weight_coverage_ratio: float | None = None,
    missing_symbols: list[str] | None = None,
    missing_top_weight_symbols: list[str] | None = None,
    rate_limited: bool = False,
    stopped_after_429: bool = False,
    remaining_symbols_skipped: int = 0,
    cache_rows_used: int = 0,
    live_rows_fetched: int = 0,
    fallback_provider: str | None = None,
    history_coverage_ratio: float | None = None,
    quote_coverage_ratio: float | None = None,
    top10_quote_coverage_ratio: float | None = None,
    top20_quote_coverage_ratio: float | None = None,
    ma200_available: bool | None = None,
    actual_quote_provider: str = "",
    actual_history_provider: str = "",
    message: str = "",
) -> Dict:
    return {
        "dataset": dataset,
        "provider": provider,
        "ok": bool(ok),
        "rows": rows,
        "symbol_coverage_ratio": symbol_coverage_ratio,
        "weight_coverage_ratio": weight_coverage_ratio,
        "missing_symbols": ",".join(missing_symbols or []),
        "missing_top_weight_symbols": ",".join(missing_top_weight_symbols or []),
        "rate_limited": bool(rate_limited),
        "stopped_after_429": bool(stopped_after_429),
        "remaining_symbols_skipped": int(remaining_symbols_skipped or 0),
        "cache_rows_used": int(cache_rows_used or 0),
        "live_rows_fetched": int(live_rows_fetched or 0),
        "fallback_provider": fallback_provider or "",
        "history_coverage_ratio": history_coverage_ratio,
        "quote_coverage_ratio": quote_coverage_ratio,
        "top10_quote_coverage_ratio": top10_quote_coverage_ratio,
        "top20_quote_coverage_ratio": top20_quote_coverage_ratio,
        "ma200_available": ma200_available,
        "actual_quote_provider": actual_quote_provider,
        "actual_history_provider": actual_history_provider,
        "message": message,
    }


def limit_window_and_value(settings: Settings, provider: str) -> tuple[str, object]:
    cfg = settings.api_limits.get(provider, {})
    if "max_credits_per_minute" in cfg:
        return "minute", cfg.get("max_credits_per_minute")
    if "minute_credits" in cfg:
        return "minute", cfg.get("minute_credits")
    if "hourly_requests" in cfg:
        return "hour", cfg.get("hourly_requests")
    if "daily_credits" in cfg:
        return "day", cfg.get("daily_credits")
    if "daily_requests" in cfg:
        return "day", cfg.get("daily_requests")
    if "max_calls_per_run" in cfg:
        return "run", cfg.get("max_calls_per_run")
    return "run", cfg.get("max_calls_per_run", "")


def symbol_list_value(symbols: list[str] | str | None) -> str:
    if symbols is None:
        return ""
    if isinstance(symbols, str):
        return symbols
    cleaned = [normalize_symbol(symbol) for symbol in symbols if str(symbol).strip()]
    if len(cleaned) <= 25:
        return ",".join(cleaned)
    return f"{len(cleaned)} symbols"


def api_usage_row(
    settings: Settings,
    run_date: str,
    provider: str,
    endpoint: str,
    calls_attempted: int,
    calls_success: int,
    symbols_requested: list[str] | str | None = None,
    symbols_loaded: list[str] | str | None = None,
    rate_limited: bool = False,
    stopped_after_429: bool = False,
    retry_after_seconds: float | None = None,
    message: str = "",
    credits_used: int | None = None,
    http_status: int | None = None,
    error_type: str = "",
    function: str = "",
    outputsize: str = "",
    adjusted: bool | None = None,
    actual_endpoint: str = "",
    production_enabled: bool = True,
    premium_blocked: bool = False,
) -> Dict:
    limit_window, limit_value = limit_window_and_value(settings, provider)
    calls_failed = max(int(calls_attempted) - int(calls_success), 0)
    return {
        "run_date": run_date,
        "provider": provider,
        "endpoint": endpoint,
        "calls_attempted": int(calls_attempted),
        "calls_success": int(calls_success),
        "calls_failed": calls_failed,
        "rate_limited": bool(rate_limited),
        "limit_window": limit_window,
        "limit_value": limit_value,
        "credits_used": int(credits_used if credits_used is not None else calls_attempted),
        "symbols_requested": symbol_list_value(symbols_requested),
        "symbols_loaded": symbol_list_value(symbols_loaded),
        "stopped_after_429": bool(stopped_after_429),
        "retry_after_seconds": retry_after_seconds,
        "http_status": http_status,
        "error_type": error_type,
        "function": function,
        "outputsize": outputsize,
        "adjusted": adjusted,
        "actual_endpoint": actual_endpoint or endpoint,
        "production_enabled": production_enabled,
        "premium_blocked": premium_blocked,
        "message": message,
    }


def ensure_api_usage_provider_rows(settings: Settings, run_date: str, rows: list[Dict], providers: list[str]) -> list[Dict]:
    seen = {row.get("provider") for row in rows}
    for provider in providers:
        if provider in seen:
            continue
        rows.append(
            api_usage_row(
                settings,
                run_date,
                provider,
                "not_called",
                0,
                0,
                message="provider not called in this daily pipeline run",
            )
        )
    return rows


def result_rate_limit_meta(result: object) -> tuple[bool, float | None]:
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        return bool(raw.get("rate_limited")), raw.get("retry_after_seconds")
    return False, None


def result_http_status(result: object) -> int | None:
    message = str(getattr(result, "message", "") or "")
    match = re.search(r"\b(4\d\d|5\d\d)\b", message)
    if match:
        return int(match.group(1))
    return 200 if getattr(result, "raw", None) is not None else None


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


def build_macro_metric_rows(fred_frames: Dict[str, pd.DataFrame]) -> list[Dict]:
    rows: list[Dict] = []
    dgs10_value, dgs10_date = fred_latest(fred_frames.get("DGS10", pd.DataFrame()))
    dgs10_prior = fred_prior_value(fred_frames.get("DGS10", pd.DataFrame()), 21)
    dgs10_change = dgs10_value - dgs10_prior if dgs10_value is not None and dgs10_prior is not None else None
    dgs2_value, dgs2_date = fred_latest(fred_frames.get("DGS2", pd.DataFrame()))
    dgs2_prior = fred_prior_value(fred_frames.get("DGS2", pd.DataFrame()), 21)
    spread = dgs10_value - dgs2_value if dgs10_value is not None and dgs2_value is not None else None
    prior_spread = dgs10_prior - dgs2_prior if dgs10_prior is not None and dgs2_prior is not None else None
    spread_change = spread - prior_spread if spread is not None and prior_spread is not None else None

    rows.extend(
        [
            {
                "metric_name": "DGS10_latest_value",
                "metric_value": dgs10_value,
                "unit_or_method": "percent yield",
                "data_date": dgs10_date,
                "source": "FRED",
            },
            {
                "metric_name": "DGS10_1M_CHANGE",
                "metric_value": dgs10_change,
                "unit_or_method": "current value minus value 21 observations ago",
                "data_date": dgs10_date,
                "source": "FRED",
            },
            {
                "metric_name": "DGS2_DGS10_SPREAD",
                "metric_value": dgs2_value - dgs10_value if dgs10_value is not None and dgs2_value is not None else None,
                "unit_or_method": "2-year yield minus 10-year yield",
                "data_date": dgs10_date or dgs2_date,
                "source": "FRED",
            },
            {
                "metric_name": "DGS10_DGS2_SPREAD",
                "metric_value": spread,
                "unit_or_method": "10-year yield minus 2-year yield",
                "data_date": dgs10_date or dgs2_date,
                "source": "FRED",
            },
            {
                "metric_name": "DGS10_DGS2_SPREAD_1M_CHANGE",
                "metric_value": spread_change,
                "unit_or_method": "current 10y-2y spread minus spread 21 observations ago",
                "data_date": dgs10_date or dgs2_date,
                "source": "FRED",
            },
        ]
    )

    for series_id, label in [("CPIAUCSL", "CPI近3次变化"), ("PCEPI", "PCE近3次变化")]:
        _, latest_date = fred_latest(fred_frames.get(series_id, pd.DataFrame()))
        changes = fred_recent_pct_changes(fred_frames.get(series_id, pd.DataFrame()), 3)
        for idx, change in enumerate(changes, start=max(1, len(changes) - 2)):
            rows.append(
                {
                    "metric_name": f"{series_id}_RECENT_PCT_CHANGE_{idx}",
                    "metric_value": change,
                    "unit_or_method": f"{label}; pct_change between consecutive observations",
                    "data_date": latest_date,
                    "source": "FRED",
                }
            )
        rows.append(
            {
                "metric_name": f"{series_id}_LATEST_PCT_CHANGE",
                "metric_value": changes[-1] if changes else None,
                "unit_or_method": f"{label}; latest pct_change between consecutive observations",
                "data_date": latest_date,
                "source": "FRED",
            }
        )

    unrate_value, unrate_date = fred_latest(fred_frames.get("UNRATE", pd.DataFrame()))
    unrate_prior = fred_prior_value(fred_frames.get("UNRATE", pd.DataFrame()), 3)
    unrate_change = unrate_value - unrate_prior if unrate_value is not None and unrate_prior is not None else None
    rows.append(
        {
            "metric_name": "UNRATE_3_OBSERVATION_CHANGE",
            "metric_value": unrate_change,
            "unit_or_method": "current unemployment rate minus value 3 observations ago",
            "data_date": unrate_date,
            "source": "FRED",
        }
    )
    return rows


def previous_latest_file(settings: Settings, filename: str) -> Path | None:
    latest = settings.paths.reports_latest_dir / filename
    if latest.exists():
        return latest
    candidates = sorted(settings.paths.processed_dir.glob(f"*/{filename}"), reverse=True)
    return candidates[0] if candidates else None


def load_previous_csv(settings: Settings, filename: str, columns: list[str]) -> pd.DataFrame:
    path = previous_latest_file(settings, filename)
    if path is None:
        return pd.DataFrame(columns=columns)
    try:
        return pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=columns)


def fetch_holdings(settings: Settings, providers: Dict[str, object], logs: list[Dict], quality_rows: list[Dict]) -> pd.DataFrame:
    invesco: InvescoProvider = providers["invesco"]  # type: ignore[assignment]
    result = invesco.qqq_holdings(settings.symbols.get("primary_etf", "QQQ"))
    logs.append({"provider": result.name, "method": "qqq_holdings", "symbol": "QQQ", "ok": result.ok, "message": result.message})
    if result.ok:
        holdings = result.data.reindex(columns=QQQ_HOLDINGS_COLUMNS)
        quality_rows.append(
            quality_row(
                "qqq_holdings",
                result.name,
                True,
                len(holdings),
                symbol_coverage_ratio=1.0,
                weight_coverage_ratio=1.0,
                message=result.message,
            )
        )
        return holdings

    previous = load_previous_csv(settings, "qqq_holdings.csv", QQQ_HOLDINGS_COLUMNS)
    if not previous.empty:
        quality_rows.append(
            quality_row(
                "qqq_holdings",
                result.name,
                False,
                len(previous),
                symbol_coverage_ratio=1.0,
                weight_coverage_ratio=1.0,
                fallback_provider="previous_csv",
                message=f"fallback to previous file; {result.message}",
            )
        )
        logs.append({"provider": result.name, "method": "qqq_holdings_fallback", "symbol": "QQQ", "ok": True, "message": "fallback to previous qqq_holdings.csv"})
        return previous.reindex(columns=QQQ_HOLDINGS_COLUMNS)

    quality_rows.append(
        quality_row(
            "qqq_holdings",
            result.name,
            False,
            0,
            symbol_coverage_ratio=0.0,
            weight_coverage_ratio=0.0,
            message=result.message,
        )
    )
    return pd.DataFrame(columns=QQQ_HOLDINGS_COLUMNS)


def normalize_symbol(symbol: object) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def build_equity_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty:
        return pd.DataFrame(columns=QQQ_EQUITY_HOLDINGS_COLUMNS)
    equities = holdings.copy()
    equities["symbol"] = equities["symbol"].map(normalize_symbol)
    equities["weight"] = pd.to_numeric(equities["weight"], errors="coerce")
    type_code = equities.get("security_type_code", pd.Series(index=equities.index, dtype="object")).astype("string").str.upper()
    has_type = type_code.notna() & type_code.ne("")
    type_match = type_code.eq("COM")
    fallback_match = (
        equities["symbol"].notna()
        & equities["symbol"].ne("")
        & ~equities["symbol"].isin(BREATH_EXCLUDE_SYMBOLS)
        & ~equities["symbol"].str.startswith("NQ")
        & ~equities["symbol"].str.endswith("_")
    )
    equities = equities[(type_match) | (~has_type & fallback_match)].copy()
    equities = equities.dropna(subset=["symbol", "weight"])
    equities = equities.drop_duplicates(subset=["symbol"], keep="first")
    equities = equities.sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)
    return equities.reindex(columns=QQQ_EQUITY_HOLDINGS_COLUMNS)


def cache_path_for_symbol(settings: Settings, symbol: str) -> Path:
    price_cache_dir = getattr(settings.paths, "price_cache_dir", settings.paths.tiingo_price_cache_dir)
    return price_cache_dir / f"{normalize_symbol(symbol)}.csv"


def load_price_cache(settings: Settings, symbol: str) -> pd.DataFrame:
    cache_path = cache_path_for_symbol(settings, symbol)
    legacy_dir = getattr(settings.paths, "tiingo_price_cache_dir", None)
    legacy_path = legacy_dir / f"{normalize_symbol(symbol)}.csv" if legacy_dir is not None else None
    if not cache_path.exists() and legacy_path is not None and legacy_path.exists():
        cache_path = legacy_path
    if not cache_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(cache_path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if "date" not in df.columns:
        return pd.DataFrame()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
    return df


def load_tiingo_cache(settings: Settings, symbol: str) -> pd.DataFrame:
    return load_price_cache(settings, symbol)


def load_tiingo_seed_from_raw(settings: Settings, symbol: str) -> pd.DataFrame:
    normalized = normalize_symbol(symbol)
    candidates = sorted(settings.paths.raw_dir.glob(f"*/tiingo_{normalized}_breadth_daily.csv"), reverse=True)
    for candidate in candidates:
        try:
            df = pd.read_csv(candidate)
        except Exception:  # noqa: BLE001
            continue
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
        return df
    return pd.DataFrame()


def merge_price_history(cache_df: pd.DataFrame, live_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frames = []
    for frame in [cache_df, live_df]:
        if frame.empty:
            continue
        source = str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns and not frame["source"].dropna().empty else "unknown"
        frames.append(normalize_price_daily(frame, symbol, source))
    if not frames:
        return pd.DataFrame(columns=PRICE_DAILY_COLUMNS)
    merged = pd.concat(frames, ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce").dt.date.astype(str)
    merged = merged.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return merged.reindex(columns=PRICE_DAILY_COLUMNS)


def write_price_cache(settings: Settings, symbol: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    write_csv(df, cache_path_for_symbol(settings, symbol))


def write_tiingo_cache(settings: Settings, symbol: str, df: pd.DataFrame) -> None:
    write_price_cache(settings, symbol, df)


def quote_map_from_batch(df: pd.DataFrame) -> dict[str, dict]:
    if df.empty:
        return {}
    quote_df = df.copy()
    quote_df["symbol"] = quote_df["symbol"].map(normalize_symbol)
    quote_df = quote_df.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="last")
    return {row["symbol"]: row.to_dict() for _, row in quote_df.iterrows()}


def quote_cache_path(settings: Settings) -> Path:
    return settings.paths.cache_dir / "quotes" / "twelve_data" / "latest_quotes.csv"


def load_quote_cache(settings: Settings) -> dict[str, dict]:
    path = quote_cache_path(settings)
    if not path.exists():
        return {}
    try:
        cached = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return {}
    if cached.empty or "symbol" not in cached.columns:
        return {}
    normalized = pd.DataFrame(
        {
            "symbol": cached["symbol"].map(normalize_symbol),
            "price": cached.get("price"),
            "change": cached.get("change"),
            "changesPercentage": cached.get("changes_percentage"),
            "volume": cached.get("volume"),
            "date": cached.get("datetime"),
            "source": cached.get("provider", "twelve_data_cache"),
            "fetched_at": cached.get("fetched_at"),
        }
    )
    return quote_map_from_batch(normalized.dropna(subset=["price"]))


def write_quote_cache(settings: Settings, quote_map: dict[str, dict], fetched_at: str) -> None:
    rows = []
    for symbol, quote in sorted(quote_map.items()):
        price = pd.to_numeric(pd.Series([quote.get("price")]), errors="coerce").iloc[0]
        if pd.isna(price):
            continue
        rows.append(
            {
                "symbol": normalize_symbol(symbol),
                "price": price,
                "change": first_present(quote, ["change"]),
                "changes_percentage": first_present(quote, ["changesPercentage", "changes_percentage"]),
                "volume": first_present(quote, ["volume"]),
                "datetime": first_present(quote, ["date", "datetime", "timestamp"]),
                "provider": quote.get("source", "twelve_data"),
                "fetched_at": quote.get("fetched_at", fetched_at),
            }
        )
    write_csv(pd.DataFrame(rows, columns=QUOTE_CACHE_COLUMNS), quote_cache_path(settings))


def first_present(row: dict, names: list[str]) -> object:
    for name in names:
        value = row.get(name)
        if value is not None and not pd.isna(value):
            return value
    return None


def build_top_holdings_quotes(
    equity_holdings: pd.DataFrame,
    quote_map: dict[str, dict],
    limit: int = 20,
    diagnostics: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if equity_holdings.empty:
        return pd.DataFrame(columns=TOP_HOLDINGS_QUOTES_COLUMNS)
    holdings = equity_holdings.copy()
    holdings["symbol"] = holdings["symbol"].map(normalize_symbol)
    holdings["weight"] = pd.to_numeric(holdings["weight"], errors="coerce").fillna(0.0)
    holdings = holdings.sort_values(["weight", "symbol"], ascending=[False, True]).head(limit)
    diagnostic_map = (
        diagnostics.assign(symbol=diagnostics["symbol"].map(normalize_symbol)).set_index("symbol").to_dict(orient="index")
        if diagnostics is not None and not diagnostics.empty
        else {}
    )

    rows: list[dict] = []
    for _, holding in holdings.iterrows():
        symbol = holding.get("symbol")
        quote = quote_map.get(symbol, {})
        is_missing = not quote
        diagnostic = diagnostic_map.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "company_name": holding.get("company_name"),
                "weight": holding.get("weight"),
                "price": first_present(quote, ["price"]),
                "change": first_present(quote, ["change"]),
                "changes_percentage": first_present(quote, ["changesPercentage", "changes_percentage", "changePercentage"]),
                "market_cap": first_present(quote, ["marketCap", "market_cap"]),
                "pe": first_present(quote, ["pe", "peRatio"]),
                "eps": first_present(quote, ["eps"]),
                "provider": quote.get("source", "twelve_data") if not is_missing else "",
                "quote_time": first_present(quote, ["timestamp", "earningsAnnouncement", "date"]),
                "is_missing": is_missing,
                "error_type": "quote_missing" if is_missing else "",
                "attempted_symbol": diagnostic.get("attempted_symbol", symbol),
                "was_api_success": bool(diagnostic.get("was_api_success", False)),
                "was_parse_success": bool(diagnostic.get("was_parse_success", False)),
                "was_merge_success": bool(diagnostic.get("was_merge_success", not is_missing)),
            }
        )
    return pd.DataFrame(rows, columns=TOP_HOLDINGS_QUOTES_COLUMNS)


def top_holding_symbols(equity_holdings: pd.DataFrame, limit: int = 20) -> list[str]:
    if equity_holdings.empty or "symbol" not in equity_holdings.columns:
        return []
    holdings = equity_holdings.copy()
    holdings["symbol"] = holdings["symbol"].map(normalize_symbol)
    holdings["weight"] = pd.to_numeric(holdings.get("weight", 0.0), errors="coerce").fillna(0.0)
    holdings = holdings[holdings["symbol"].notna() & holdings["symbol"].ne("")]
    return holdings.sort_values(["weight", "symbol"], ascending=[False, True]).head(limit)["symbol"].tolist()


def top_holdings_quote_quality(
    equity_holdings: pd.DataFrame,
    quotes: pd.DataFrame,
    provider: str = "twelve_data",
    rate_limited: bool = False,
    stopped_after_429: bool = False,
) -> Dict:
    top20 = top_holding_symbols(equity_holdings, 20)
    top10 = top_holding_symbols(equity_holdings, 10)
    if quotes.empty or not top20:
        available: set[str] = set()
    else:
        quote_df = quotes.copy()
        quote_df["symbol"] = quote_df["symbol"].map(normalize_symbol)
        available = set(quote_df.loc[~quote_df["is_missing"].astype(bool), "symbol"])
    missing20 = [symbol for symbol in top20 if symbol not in available]
    missing10 = [symbol for symbol in top10 if symbol not in available]
    requested = len(top20)
    return quality_row(
        "top_holdings_quotes",
        provider,
        bool(requested and len(missing20) < requested),
        len(quotes),
        symbol_coverage_ratio=(requested - len(missing20)) / requested if requested else 0.0,
        weight_coverage_ratio=(requested - len(missing20)) / requested if requested else 0.0,
        missing_symbols=missing20,
        missing_top_weight_symbols=missing10,
        rate_limited=rate_limited,
        stopped_after_429=stopped_after_429,
        fallback_provider="quote_cache",
        quote_coverage_ratio=(requested - len(missing20)) / requested if requested else 0.0,
        top10_quote_coverage_ratio=(len(top10) - len(missing10)) / len(top10) if top10 else 0.0,
        top20_quote_coverage_ratio=(requested - len(missing20)) / requested if requested else 0.0,
        actual_quote_provider="twelve_data",
        message=f"{requested - len(missing20)}/{requested} top20 quotes available; {len(top10) - len(missing10)}/{len(top10)} top10 quotes available",
    )


def fetch_twelve_data_quotes(
    settings: Settings,
    twelve_data: TwelveDataProvider,
    run_date: str,
    equity_holdings: pd.DataFrame,
    raw_dir: Path | None = None,
    limit: int = 20,
) -> tuple[dict[str, dict], pd.DataFrame, Dict]:
    quote_map: dict[str, dict] = {}
    top_symbols = top_holding_symbols(equity_holdings, limit)
    holdings_by_symbol = (
        equity_holdings.assign(symbol=equity_holdings["symbol"].map(normalize_symbol))
        .drop_duplicates("symbol")
        .set_index("symbol")
        .to_dict(orient="index")
        if not equity_holdings.empty
        else {}
    )
    cfg = settings.api_limits.get("twelve_data", {})
    max_credits = int(cfg.get("quote_max_credits_per_run", 40))
    batch_size = int(cfg.get("quote_batch_size", cfg.get("max_credits_per_minute", 8)))
    sleep_seconds = float(cfg.get("quote_sleep_seconds_between_batches", cfg.get("sleep_seconds_between_batches", 70)))
    attempted: list[str] = []
    loaded: list[str] = []
    messages: list[str] = []
    attempts: dict[str, dict] = {}
    failed_raw: dict[str, object] = {}
    rate_limited = False
    retry_after_seconds = None

    if not getattr(twelve_data, "available", False):
        cached = load_quote_cache(settings)
        quote_map.update({symbol: cached[symbol] for symbol in top_symbols if symbol in cached})
        diagnostics = build_quote_diagnostics(top_symbols, holdings_by_symbol, attempts, quote_map)
        return (
            quote_map,
            diagnostics,
            api_usage_row(
                settings,
                run_date,
                "twelve_data",
                "quote",
                0,
                0,
                symbols_requested=top_symbols,
                symbols_loaded=list(quote_map),
                error_type="missing_api_key",
                actual_endpoint="quote",
                message="missing Twelve Data API key; used quote cache",
            ),
        )

    for round_number in (1, 2):
        candidates = [symbol for symbol in top_symbols if symbol not in quote_map]
        if not candidates or rate_limited:
            break
        for symbol in candidates:
            if len(attempted) >= max_credits or rate_limited:
                break
            if attempted and batch_size > 0 and len(attempted) % batch_size == 0:
                import time

                time.sleep(sleep_seconds)
            attempted.append(symbol)
            result = twelve_data.quote(symbol)
            result_rate_limited, retry_after = result_rate_limit_meta(result)
            api_success = bool(result.ok and not result.data.empty)
            parsed = quote_map_from_batch(result.data) if api_success else {}
            parsed_quote = parsed.get(normalize_symbol(symbol))
            price = pd.to_numeric(pd.Series([(parsed_quote or {}).get("price")]), errors="coerce").iloc[0]
            parse_success = parsed_quote is not None and not pd.isna(price)
            attempts[symbol] = {
                "attempted_symbol": symbol,
                "http_status": result_http_status(result),
                "api_status": "success" if api_success else "error",
                "raw_error": "" if api_success else result.message,
                "parse_error": "" if parse_success else ("missing normalized symbol or numeric price" if api_success else ""),
                "was_api_success": api_success,
                "was_parse_success": parse_success,
            }
            if parse_success:
                quote_map[symbol] = parsed_quote
                if symbol not in loaded:
                    loaded.append(symbol)
                messages.append(f"{symbol}: quote loaded on round {round_number}")
            else:
                failed_raw[symbol] = result.raw
                messages.append(f"{symbol}: {result.message or 'parse failed'}")
            if result_rate_limited:
                rate_limited = True
                retry_after_seconds = retry_after

    cached = load_quote_cache(settings)
    for symbol in top_symbols:
        if symbol not in quote_map and symbol in cached:
            quote_map[symbol] = cached[symbol]
    write_quote_cache(settings, quote_map, datetime.now(UTC).isoformat(timespec="seconds"))

    final_missing = [symbol for symbol in top_symbols if symbol not in quote_map]
    debug_dir = settings.paths.root / "data" / "debug" / "twelve_data_quotes" / run_date
    for symbol in final_missing:
        raw = failed_raw.get(symbol)
        if isinstance(raw, dict):
            write_json(raw, debug_dir / f"{symbol}.json")

    diagnostics = build_quote_diagnostics(top_symbols, holdings_by_symbol, attempts, quote_map)
    return (
        quote_map,
        diagnostics,
        api_usage_row(
            settings,
            run_date,
            "twelve_data",
            "quote",
            len(attempted),
            len(loaded),
            symbols_requested=attempted,
            symbols_loaded=loaded,
            rate_limited=rate_limited,
            stopped_after_429=rate_limited,
            retry_after_seconds=retry_after_seconds,
            credits_used=len(attempted),
            http_status=429 if rate_limited else (200 if loaded else None),
            error_type="rate_limited" if rate_limited else ("success" if loaded else "api_error"),
            actual_endpoint="quote",
            message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
        ),
    )


def build_quote_diagnostics(
    symbols: list[str],
    holdings_by_symbol: dict[str, dict],
    attempts: dict[str, dict],
    quote_map: dict[str, dict],
) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        attempt = attempts.get(symbol, {})
        merged = symbol in quote_map
        holding = holdings_by_symbol.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "company_name": holding.get("company_name"),
                "weight": holding.get("weight"),
                "provider": "twelve_data",
                "endpoint": "quote",
                "attempted_symbol": attempt.get("attempted_symbol", symbol),
                "http_status": attempt.get("http_status"),
                "api_status": attempt.get("api_status", "cache" if merged else "not_attempted"),
                "raw_error": attempt.get("raw_error", ""),
                "parse_error": attempt.get("parse_error", ""),
                "was_api_success": bool(attempt.get("was_api_success", False)),
                "was_parse_success": bool(attempt.get("was_parse_success", False)),
                "was_merge_success": merged,
                "final_missing": not merged,
            }
        )
    return pd.DataFrame(rows, columns=QUOTE_FAILURE_COLUMNS)


def valid_history_row_count(df: pd.DataFrame) -> int:
    if df.empty or "date" not in df.columns:
        return 0
    return len(df.dropna(subset=["date"]))


def cached_or_seed_history_row_count(settings: Settings, symbol: str) -> int:
    cache_df = load_tiingo_cache(settings, symbol)
    if not cache_df.empty:
        return valid_history_row_count(cache_df)
    return valid_history_row_count(load_tiingo_seed_from_raw(settings, symbol))


def compute_missing_top_weight_symbols(holdings: pd.DataFrame, missing_symbols: list[str]) -> list[str]:
    if holdings.empty or not missing_symbols:
        return []
    ranked = holdings.copy()
    ranked["symbol"] = ranked["symbol"].map(normalize_symbol)
    ranked["weight"] = pd.to_numeric(ranked["weight"], errors="coerce")
    ranked = ranked[ranked["symbol"].isin(missing_symbols)].sort_values("weight", ascending=False)
    return ranked["symbol"].head(TOP_WEIGHT_MISSING_LIMIT).tolist()


def build_breadth_metrics(
    price_frames: dict[str, pd.DataFrame],
    quote_map: dict[str, dict] | None = None,
    source_name: str = "tiingo_cache_only",
) -> pd.DataFrame:
    quote_map = quote_map or {}
    records: list[dict] = []
    latest_dates: list[str] = []
    for symbol, df in price_frames.items():
        if df.empty:
            continue
        price_col = "adjusted_close" if "adjusted_close" in df.columns else "adjClose" if "adjClose" in df.columns else "close"
        if price_col not in df.columns:
            continue
        d = df.dropna(subset=[price_col]).sort_values("date")
        if len(d) < 2:
            continue
        close = d[price_col].astype(float)
        latest_dates.append(str(d.iloc[-1]["date"]))
        quote = quote_map.get(symbol, {})
        latest = pd.to_numeric(pd.Series([quote.get("price")]), errors="coerce").iloc[0]
        previous = pd.to_numeric(pd.Series([quote.get("previousClose")]), errors="coerce").iloc[0]
        if pd.isna(latest):
            latest = close.iloc[-1]
        if pd.isna(previous):
            previous = close.iloc[-2]
        records.append(
            {
                "symbol": symbol,
                "up": latest > previous,
                "down": latest < previous,
                "above_ma20": latest > close.tail(20).mean() if len(close) >= 20 else None,
                "above_ma50": latest > close.tail(50).mean() if len(close) >= 50 else None,
                "above_ma200": latest > close.tail(200).mean() if len(close) >= 200 else None,
                "new_high_20d": latest >= close.tail(20).max() if len(close) >= 20 else None,
                "new_low_20d": latest <= close.tail(20).min() if len(close) >= 20 else None,
            }
        )

    data_date = max(latest_dates) if latest_dates else None

    def count_true(field: str) -> tuple[int | None, int]:
        valid = [r[field] for r in records if r[field] is not None]
        return (sum(1 for x in valid if x), len(valid))

    rows: list[list[object]] = []
    for metric, field in [
        ("advancing_count", "up"),
        ("declining_count", "down"),
        ("above_20d_ma_ratio", "above_ma20"),
        ("above_50d_ma_ratio", "above_ma50"),
        ("above_200d_ma_ratio", "above_ma200"),
        ("new_high_20d_count", "new_high_20d"),
        ("new_low_20d_count", "new_low_20d"),
    ]:
        true_count, denominator = count_true(field)
        value = true_count / denominator if metric.endswith("_ratio") and denominator else true_count
        rows.append([metric, value, denominator, data_date, source_name, pd.isna(value)])
    advancing, denominator = count_true("up")
    rows.insert(2, ["advancing_ratio", advancing / denominator if denominator else None, denominator, data_date, source_name, denominator == 0])
    return pd.DataFrame(rows, columns=BREADTH_METRICS_COLUMNS)


def fetch_tiingo_history_for_breadth(
    settings: Settings,
    tiingo: TiingoProvider,
    symbols: list[str],
    run_date: str,
    raw_dir: Path,
    logs: list[Dict],
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    rate_limited = False
    stopped_after_429 = False
    remaining_symbols_skipped = 0
    cache_rows_used = 0
    live_rows_fetched = 0
    backfill_budget = MAX_BACKFILL_SYMBOLS_PER_RUN

    for index, symbol in enumerate(symbols):
        cache_df = load_tiingo_cache(settings, symbol)
        if cache_df.empty:
            cache_df = load_tiingo_seed_from_raw(settings, symbol)
            if not cache_df.empty:
                write_tiingo_cache(settings, symbol, cache_df)
        cache_rows_used += len(cache_df)
        cache_ready = valid_history_row_count(cache_df) >= MIN_HISTORY_ROWS
        live_df = pd.DataFrame()

        if not cache_ready and backfill_budget <= 0:
            if valid_history_row_count(cache_df) >= 2:
                frames[symbol] = cache_df
            else:
                missing.append(symbol)
            continue

        if rate_limited:
            if valid_history_row_count(cache_df) >= 2:
                frames[symbol] = cache_df
            else:
                missing.append(symbol)
            continue

        should_fetch = cache_df.empty or not cache_ready
        start = (datetime.fromisoformat(run_date) - timedelta(days=FULL_HISTORY_DAYS)).date().isoformat()
        if cache_ready:
            latest_cached_date = pd.to_datetime(cache_df["date"], errors="coerce").dropna().max()
            incremental_start = (latest_cached_date.date() - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)).isoformat() if latest_cached_date is not pd.NaT else start
            start = incremental_start
            should_fetch = True

        if should_fetch:
            if not cache_ready:
                backfill_budget -= 1
            result = tiingo.daily_prices(symbol, start_date=start, end_date=run_date)
            logs.append({"provider": result.name, "method": "breadth_daily_prices", "symbol": symbol, "ok": result.ok, "message": result.message})
            raw_meta = result.raw if isinstance(result.raw, dict) else {}
            if result.ok:
                live_df = result.data
                live_rows_fetched += len(live_df)
                write_csv(live_df, raw_dir / f"tiingo_{symbol}_breadth_daily.csv")
            elif raw_meta.get("rate_limited"):
                merged = merge_price_history(cache_df, live_df, symbol)
                if valid_history_row_count(merged) >= 2:
                    write_tiingo_cache(settings, symbol, merged)
                    frames[symbol] = merged
                else:
                    missing.append(symbol)
                rate_limited = True
                stopped_after_429 = True
                remaining_symbols_skipped = len(symbols) - index - 1
                continue
            else:
                missing.append(symbol)

        merged = merge_price_history(cache_df, live_df, symbol)
        if merged.empty or valid_history_row_count(merged) < 2:
            if symbol not in missing:
                missing.append(symbol)
            continue
        write_tiingo_cache(settings, symbol, merged)
        frames[symbol] = merged

    return frames, {
        "missing_symbols": missing,
        "rate_limited": rate_limited,
        "stopped_after_429": stopped_after_429,
        "remaining_symbols_skipped": remaining_symbols_skipped,
        "cache_rows_used": cache_rows_used,
        "live_rows_fetched": live_rows_fetched,
        "backfill_budget_remaining": backfill_budget,
    }


def load_cached_history_for_breadth(settings: Settings, symbols: list[str]) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    cache_rows_used = 0

    for symbol in symbols:
        cache_df = load_tiingo_cache(settings, symbol)
        if cache_df.empty:
            cache_df = load_tiingo_seed_from_raw(settings, symbol)
            if not cache_df.empty:
                write_tiingo_cache(settings, symbol, cache_df)
        cache_rows_used += len(cache_df)
        if valid_history_row_count(cache_df) >= 2:
            frames[symbol] = cache_df
        else:
            missing.append(symbol)

    return frames, {
        "missing_symbols": missing,
        "rate_limited": False,
        "stopped_after_429": False,
        "remaining_symbols_skipped": 0,
        "cache_rows_used": cache_rows_used,
        "live_rows_fetched": 0,
    }


def fetch_breadth(
    settings: Settings,
    equity_holdings: pd.DataFrame,
    run_date: str,
    logs: list[Dict],
    quality_rows: list[Dict],
    quote_map: dict[str, dict] | None = None,
) -> pd.DataFrame:
    if equity_holdings.empty or "symbol" not in equity_holdings.columns:
        quality_rows.append(
            quality_row(
                "breadth_metrics",
                "insufficient_price_coverage",
                False,
                0,
                symbol_coverage_ratio=0.0,
                weight_coverage_ratio=0.0,
                message="no equity holdings available",
            )
        )
        return pd.DataFrame(columns=BREADTH_METRICS_COLUMNS)

    quote_map = quote_map or {}
    holdings = equity_holdings.copy()
    holdings["symbol"] = holdings["symbol"].map(normalize_symbol)
    holdings["weight"] = pd.to_numeric(holdings["weight"], errors="coerce").fillna(0.0)
    symbol_priority = []
    for symbol in holdings["symbol"].dropna().astype(str).unique().tolist():
        history_rows = cached_or_seed_history_row_count(settings, symbol)
        symbol_priority.append((symbol, history_rows >= MIN_HISTORY_ROWS, history_rows))
    priority_map = {symbol: (has_ready_cache, history_rows) for symbol, has_ready_cache, history_rows in symbol_priority}
    holdings["has_ready_cache"] = holdings["symbol"].map(lambda symbol: priority_map.get(symbol, (False, 0))[0])
    holdings["cached_history_rows"] = holdings["symbol"].map(lambda symbol: priority_map.get(symbol, (False, 0))[1])
    ordered = holdings.sort_values(["has_ready_cache", "cached_history_rows", "weight", "symbol"], ascending=[False, False, False, True])
    symbols = ordered["symbol"].dropna().astype(str).unique().tolist()

    history_frames, history_meta = load_cached_history_for_breadth(settings, symbols)
    fetched_symbols = sorted(history_frames.keys())
    logs.append(
        {
            "provider": "tiingo_cache",
            "method": "load_cached_history_for_breadth",
            "symbol": symbol_list_value(symbols),
            "ok": bool(fetched_symbols),
            "message": f"{len(fetched_symbols)}/{len(symbols)} symbols loaded from local cache/raw seed",
        }
    )
    history_sources = {
        str(source)
        for frame in history_frames.values()
        if "source" in frame.columns
        for source in frame["source"].dropna().astype(str).unique()
    }
    quote_provider = "twelve_data_quote" if quote_map else ""
    if history_sources == {"tiingo"}:
        source_name = "tiingo_cache+twelve_data_quote" if quote_provider else "tiingo_cache_only"
    elif history_sources:
        source_name = "mixed_history_cache+twelve_data_quote" if quote_provider else "mixed_history_cache"
    else:
        source_name = "insufficient_price_coverage"

    usable_frames: dict[str, pd.DataFrame] = {}
    missing_symbols = list(dict.fromkeys(history_meta["missing_symbols"]))
    for symbol in fetched_symbols:
        frame = history_frames[symbol]
        usable_frames[symbol] = frame

    metrics = build_breadth_metrics(usable_frames, quote_map, source_name=source_name)
    total_symbols = len(symbols)
    used_symbols = sorted(usable_frames.keys())
    used_weight = holdings[holdings["symbol"].isin(used_symbols)]["weight"].sum()
    total_weight = holdings["weight"].sum()
    symbol_coverage_ratio = len(used_symbols) / total_symbols if total_symbols else 0.0
    weight_coverage_ratio = used_weight / total_weight if total_weight else 0.0
    missing_symbols = sorted(set(missing_symbols) | (set(symbols) - set(used_symbols)))
    missing_top_weight_symbols = compute_missing_top_weight_symbols(holdings, missing_symbols)

    quality_ok = not metrics.empty
    quality_message = f"{len(used_symbols)}/{total_symbols} symbols with history+quote coverage"
    if missing_top_weight_symbols:
        quality_message = f"{quality_message}; coverage insufficient for top-weight symbols"
    quality_rows.append(
        quality_row(
            "breadth_metrics",
            source_name,
            quality_ok,
            len(metrics),
            symbol_coverage_ratio=symbol_coverage_ratio,
            weight_coverage_ratio=weight_coverage_ratio,
            missing_symbols=missing_symbols,
            missing_top_weight_symbols=missing_top_weight_symbols,
            rate_limited=bool(history_meta["rate_limited"]),
            stopped_after_429=bool(history_meta["stopped_after_429"]),
            remaining_symbols_skipped=int(history_meta["remaining_symbols_skipped"]),
            cache_rows_used=int(history_meta["cache_rows_used"]),
            live_rows_fetched=int(history_meta["live_rows_fetched"]),
            fallback_provider=quote_provider,
            history_coverage_ratio=symbol_coverage_ratio,
            quote_coverage_ratio=len(set(quote_map) & set(used_symbols)) / len(used_symbols) if used_symbols else 0.0,
            actual_quote_provider="twelve_data" if quote_map else "",
            actual_history_provider=source_name.split("+")[0],
            message=quality_message,
        )
    )
    return metrics


def fetch_qqq_price_history(
    settings: Settings,
    providers: Dict[str, object],
    run_date: str,
    raw_dir: Path,
    logs: list[Dict],
    api_usage_rows: list[Dict],
    quality_rows: list[Dict],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbol = settings.symbols.get("primary_etf", "QQQ")
    cache_df = load_price_cache(settings, symbol)
    av: AlphaVantageProvider = providers["alpha_vantage"]  # type: ignore[assignment]
    outputsize = provider_config(settings, "alpha_vantage").get("default_outputsize", "compact")
    av_result = av.daily(symbol, outputsize=outputsize)
    logs.append({"provider": av_result.name, "method": "daily", "symbol": symbol, "ok": av_result.ok, "message": av_result.message})
    av_rate_limited, av_retry_after = result_rate_limit_meta(av_result)
    api_usage_rows.append(
        api_usage_row(
            settings,
            run_date,
            "alpha_vantage",
            "TIME_SERIES_DAILY",
            1 if av.available else 0,
            1 if av_result.ok else 0,
            symbols_requested=symbol,
            symbols_loaded=symbol if av_result.ok else "",
            rate_limited=av_rate_limited,
            stopped_after_429=av_rate_limited,
            retry_after_seconds=av_retry_after,
            http_status=200 if av_result.ok else None,
            error_type="success" if av_result.ok else ("rate_limited" if av_rate_limited else "api_error"),
            function="TIME_SERIES_DAILY",
            outputsize=str(outputsize),
            adjusted=False,
            actual_endpoint="TIME_SERIES_DAILY",
            message=av_result.message,
        )
    )
    if av_result.ok:
        write_csv(av_result.data, raw_dir / f"alpha_vantage_{symbol}_daily.csv")
        cache_df = merge_price_history(cache_df, av_result.data, symbol)

    tiingo_attempted = 0
    tiingo_success = 0
    twelve_attempted = 0
    twelve_success = 0
    if valid_history_row_count(cache_df) < QQQ_OUTPUT_ROWS:
        start = (datetime.fromisoformat(run_date) - timedelta(days=FULL_HISTORY_DAYS)).date().isoformat()
        tiingo: TiingoProvider = providers["tiingo"]  # type: ignore[assignment]
        if tiingo.available:
            tiingo_attempted = 1
            result = tiingo.daily_prices(symbol, start_date=start, end_date=run_date)
            logs.append({"provider": result.name, "method": "qqq_cache_initialize", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok and not result.data.empty:
                tiingo_success = 1
                write_csv(result.data, raw_dir / f"tiingo_{symbol}_cache_initialize.csv")
                cache_df = merge_price_history(cache_df, result.data, symbol)

    if valid_history_row_count(cache_df) < QQQ_OUTPUT_ROWS:
        twelve_data: TwelveDataProvider = providers["twelve_data"]  # type: ignore[assignment]
        if twelve_data.available:
            twelve_attempted = 1
            result = twelve_data.time_series(symbol, outputsize=QQQ_OUTPUT_ROWS, interval="1day")
            logs.append({"provider": result.name, "method": "qqq_cache_initialize", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok and not result.data.empty:
                twelve_success = 1
                write_csv(result.data, raw_dir / f"twelve_data_{symbol}_cache_initialize.csv")
                cache_df = merge_price_history(cache_df, result.data, symbol)

    api_usage_rows.append(
        api_usage_row(
            settings,
            run_date,
            "tiingo",
            "qqq_cache_initialize",
            tiingo_attempted,
            tiingo_success,
            symbols_requested=symbol if tiingo_attempted else "",
            symbols_loaded=symbol if tiingo_success else "",
            message="called only when QQQ cache had fewer than 260 rows",
        )
    )
    api_usage_rows.append(
        api_usage_row(
            settings,
            run_date,
            "twelve_data",
            "qqq_cache_initialize",
            twelve_attempted,
            twelve_success,
            symbols_requested=symbol if twelve_attempted else "",
            symbols_loaded=symbol if twelve_success else "",
            message="fallback initialization when QQQ cache remained below 260 rows",
        )
    )

    if not cache_df.empty:
        write_price_cache(settings, symbol, cache_df)
    output = cache_df.sort_values("date").tail(QQQ_OUTPUT_ROWS).reset_index(drop=True)
    metric_source = "alpha_vantage_compact+local_cache"
    metrics = pd.DataFrame([summarize_price(symbol, output, metric_source)], columns=PRICE_METRICS_COLUMNS)
    rows = valid_history_row_count(output)
    quality_rows.append(
        quality_row(
            "price_daily",
            metric_source,
            rows > 0,
            rows,
            symbol_coverage_ratio=1.0 if rows else 0.0,
            weight_coverage_ratio=1.0 if rows else 0.0,
            missing_symbols=[] if rows else [symbol],
            cache_rows_used=rows,
            live_rows_fetched=len(av_result.data) if av_result.ok else 0,
            fallback_provider="tiingo+twelve_data",
            history_coverage_ratio=min(rows / QQQ_OUTPUT_ROWS, 1.0),
            ma200_available=bool(not metrics.empty and pd.notna(metrics.loc[0, "ma_200"])),
            actual_history_provider=metric_source,
            message="QQQ cache ready" if rows >= QQQ_OUTPUT_ROWS else "insufficient_history_for_ma200",
        )
    )
    return output.reindex(columns=PRICE_DAILY_COLUMNS), metrics


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
    quality_rows: list[Dict] = []
    api_usage_rows: list[Dict] = []
    price_daily, price_metrics = fetch_qqq_price_history(
        settings,
        providers,
        run_date,
        raw_dir,
        logs,
        api_usage_rows,
        quality_rows,
    )
    write_csv(price_daily, processed_dir / "price_daily.csv")
    write_csv(price_metrics, processed_dir / "price_metrics.csv")

    macro_daily_rows = []
    fred_frames: Dict[str, pd.DataFrame] = {}
    fred: FREDProvider = providers["fred"]  # type: ignore[assignment]
    if settings.pipeline.get("run", {}).get("fetch_fred_macro", True):
        series_map = settings.fred_series.get("series", {})
        fred_requested = list(series_map.keys())
        fred_success: list[str] = []
        fred_messages: list[str] = []
        fred_rate_limited = False
        fred_retry_after = None
        for series_id, name in series_map.items():
            result = fred.observations(series_id, limit=365)
            logs.append({"provider": result.name, "method": "observations", "symbol": series_id, "ok": result.ok, "message": result.message})
            rate_limited, retry_after = result_rate_limit_meta(result)
            fred_rate_limited = fred_rate_limited or rate_limited
            fred_retry_after = retry_after if retry_after is not None else fred_retry_after
            fred_messages.append(f"{series_id}: {result.message}")
            if result.ok:
                fred_success.append(series_id)
                write_csv(result.data, raw_dir / f"fred_{series_id}.csv")
                fred_frames[series_id] = result.data
                latest = latest_value(result.data)
                last_date = result.data.sort_values("date").iloc[-1]["date"] if not result.data.empty else None
                macro_daily_rows.append(
                    {
                        "series_id": series_id,
                        "name": name,
                        "latest_date": last_date,
                        "latest_value": latest,
                        "source": "FRED",
                    }
                )
            quality_rows.append(
                quality_row(
                    "macro_daily",
                    result.name,
                    result.ok,
                    len(result.data),
                    symbol_coverage_ratio=1.0 if result.ok else 0.0,
                    weight_coverage_ratio=1.0 if result.ok else 0.0,
                    missing_symbols=[] if result.ok else [series_id],
                    message=result.message,
                )
            )
        api_usage_rows.append(
            api_usage_row(
                settings,
                run_date,
                "fred",
                "series_observations",
                len(fred_requested) if fred.available else 0,
                len(fred_success),
                symbols_requested=fred_requested,
                symbols_loaded=fred_success,
                rate_limited=fred_rate_limited,
                stopped_after_429=fred_rate_limited,
                retry_after_seconds=fred_retry_after,
                message="; ".join(fred_messages[:3]) + ("; ..." if len(fred_messages) > 3 else ""),
            )
        )
    macro_daily = pd.DataFrame(macro_daily_rows, columns=MACRO_DAILY_COLUMNS)
    macro_metrics = pd.DataFrame(build_macro_metric_rows(fred_frames), columns=MACRO_METRICS_COLUMNS)
    write_csv(macro_daily, processed_dir / "macro_daily.csv")
    write_csv(macro_metrics, processed_dir / "macro_metrics.csv")

    qqq_holdings = pd.DataFrame(columns=QQQ_HOLDINGS_COLUMNS)
    if settings.pipeline.get("run", {}).get("fetch_qqq_holdings", True):
        qqq_holdings = fetch_holdings(settings, providers, logs, quality_rows)
        api_usage_rows.append(
            api_usage_row(
                settings,
                run_date,
                "invesco",
                "qqq_holdings",
                1,
                1 if not qqq_holdings.empty else 0,
                symbols_requested="QQQ",
                symbols_loaded="QQQ" if not qqq_holdings.empty else "",
                message=f"holdings rows={len(qqq_holdings)}",
            )
        )
    qqq_holdings = qqq_holdings.reindex(columns=QQQ_HOLDINGS_COLUMNS)
    qqq_equity_holdings = build_equity_holdings(qqq_holdings)
    write_csv(qqq_holdings, processed_dir / "qqq_holdings.csv")
    write_csv(qqq_equity_holdings, processed_dir / "qqq_equity_holdings.csv")

    if not qqq_holdings.empty:
        quality_rows.append(
            quality_row(
                "qqq_equity_holdings",
                "invesco_filter",
                True,
                len(qqq_equity_holdings),
                symbol_coverage_ratio=len(qqq_equity_holdings) / len(qqq_holdings) if len(qqq_holdings) else 0.0,
                weight_coverage_ratio=pd.to_numeric(qqq_equity_holdings["weight"], errors="coerce").fillna(0.0).sum()
                / pd.to_numeric(qqq_holdings["weight"], errors="coerce").fillna(0.0).sum()
                if pd.to_numeric(qqq_holdings["weight"], errors="coerce").fillna(0.0).sum()
                else 0.0,
                missing_symbols=sorted(set(qqq_holdings["symbol"].astype(str)) - set(qqq_equity_holdings["symbol"].astype(str))),
                message="equity filter applied for breadth stock pool",
            )
        )

    twelve_data: TwelveDataProvider = providers["twelve_data"]  # type: ignore[assignment]
    fmp_summary = pd.DataFrame(columns=["symbol", "ok", "rows", "message"])
    top_holdings_quotes = pd.DataFrame(columns=TOP_HOLDINGS_QUOTES_COLUMNS)
    api_usage_rows.append(
        api_usage_row(
            settings,
            run_date,
            "fmp",
            "diagnostic_only",
            0,
            0,
            production_enabled=False,
            actual_endpoint="diagnostic_only",
            message="FMP production quote collection disabled; use provider capability probe",
        )
    )

    quote_map, quote_failures, twelve_quote_usage = fetch_twelve_data_quotes(
        settings,
        twelve_data,
        run_date,
        qqq_equity_holdings,
        raw_dir=raw_dir,
        limit=20,
    )
    api_usage_rows.append(twelve_quote_usage)
    top_holdings_quotes = build_top_holdings_quotes(qqq_equity_holdings, quote_map, limit=20, diagnostics=quote_failures)
    quality_rows.append(
        top_holdings_quote_quality(
            qqq_equity_holdings,
            top_holdings_quotes,
            rate_limited=bool(twelve_quote_usage.get("rate_limited")),
            stopped_after_429=bool(twelve_quote_usage.get("stopped_after_429")),
        )
    )
    write_csv(top_holdings_quotes, processed_dir / "top_holdings_quotes.csv")
    write_csv(quote_failures, processed_dir / "quote_failures.csv")

    breadth_metrics = pd.DataFrame(columns=BREADTH_METRICS_COLUMNS)
    if settings.pipeline.get("run", {}).get("fetch_breadth_metrics", True):
        breadth_metrics = fetch_breadth(settings, qqq_equity_holdings, run_date, logs, quality_rows, quote_map)
    breadth_metrics = breadth_metrics.reindex(columns=BREADTH_METRICS_COLUMNS)
    write_csv(breadth_metrics, processed_dir / "breadth_metrics.csv")

    logs_df = pd.DataFrame(logs)
    data_quality = pd.DataFrame(quality_rows, columns=DATA_QUALITY_COLUMNS)
    api_usage_rows = ensure_api_usage_provider_rows(settings, run_date, api_usage_rows, ["alpha_vantage", "fred", "fmp", "tiingo", "twelve_data", "invesco"])
    api_usage = pd.DataFrame(api_usage_rows, columns=API_USAGE_COLUMNS)
    write_csv(logs_df, processed_dir / "run_log.csv")
    write_csv(data_quality, processed_dir / "data_quality.csv")
    write_csv(api_usage, processed_dir / "api_usage.csv")

    model_input_metrics = build_model_input_metrics_v2(price_metrics, macro_daily, macro_metrics, fmp_summary, breadth_metrics, data_quality)
    write_csv(model_input_metrics, processed_dir / "model_input_metrics.csv")
    write_csv(model_input_metrics, latest_dir / "model_input_metrics.csv")
    write_csv(price_daily, latest_dir / "price_daily.csv")
    write_csv(price_metrics, latest_dir / "price_metrics.csv")
    write_csv(macro_daily, latest_dir / "macro_daily.csv")
    write_csv(macro_metrics, latest_dir / "macro_metrics.csv")
    write_csv(qqq_holdings, latest_dir / "qqq_holdings.csv")
    write_csv(qqq_equity_holdings, latest_dir / "qqq_equity_holdings.csv")
    write_csv(breadth_metrics, latest_dir / "breadth_metrics.csv")
    write_csv(top_holdings_quotes, latest_dir / "top_holdings_quotes.csv")
    write_csv(quote_failures, latest_dir / "quote_failures.csv")
    write_csv(data_quality, latest_dir / "data_quality.csv")
    write_csv(api_usage, latest_dir / "api_usage.csv")
    write_csv(logs_df, latest_dir / "run_log.csv")

    xlsx_path = latest_dir / "nasdaq100_qqq_daily_tracker.xlsx"
    write_excel(
        xlsx_path,
        {
            "价格日线": price_daily,
            "价格指标": price_metrics,
            "宏观原始": macro_daily,
            "宏观指标": macro_metrics,
            "QQQ持仓": qqq_holdings,
            "QQQ股票池": qqq_equity_holdings,
            "市场广度": breadth_metrics,
            "前20持仓报价": top_holdings_quotes,
            "报价失败诊断": quote_failures,
            "数据质量": data_quality,
            "API调用": api_usage,
            "模型输入指标": model_input_metrics,
            "运行日志": logs_df,
        },
    )

    archive_xlsx_path = archive_dir / f"nasdaq100_qqq_daily_tracker_{run_date}.xlsx"
    shutil.copy2(xlsx_path, archive_xlsx_path)

    manifest = {
        "as_of": run_date,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "latest_files": {
            "excel_report": rel_path(xlsx_path, settings.paths.root),
            "manifest_json": rel_path(latest_dir / "manifest.json", settings.paths.root),
            "model_input_metrics_csv": rel_path(latest_dir / "model_input_metrics.csv", settings.paths.root),
            "price_daily_csv": rel_path(latest_dir / "price_daily.csv", settings.paths.root),
            "price_metrics_csv": rel_path(latest_dir / "price_metrics.csv", settings.paths.root),
            "macro_daily_csv": rel_path(latest_dir / "macro_daily.csv", settings.paths.root),
            "macro_metrics_csv": rel_path(latest_dir / "macro_metrics.csv", settings.paths.root),
            "qqq_holdings_csv": rel_path(latest_dir / "qqq_holdings.csv", settings.paths.root),
            "qqq_equity_holdings_csv": rel_path(latest_dir / "qqq_equity_holdings.csv", settings.paths.root),
            "breadth_metrics_csv": rel_path(latest_dir / "breadth_metrics.csv", settings.paths.root),
            "top_holdings_quotes_csv": rel_path(latest_dir / "top_holdings_quotes.csv", settings.paths.root),
            "quote_failures_csv": rel_path(latest_dir / "quote_failures.csv", settings.paths.root),
            "provider_capability_probe_csv": rel_path(latest_dir / "provider_capability_probe.csv", settings.paths.root),
            "cache_quality_csv": rel_path(latest_dir / "cache_quality.csv", settings.paths.root),
            "data_quality_csv": rel_path(latest_dir / "data_quality.csv", settings.paths.root),
            "api_usage_csv": rel_path(latest_dir / "api_usage.csv", settings.paths.root),
            "run_log_csv": rel_path(latest_dir / "run_log.csv", settings.paths.root),
        },
        "quality_summary": data_quality.to_dict(orient="records"),
        "api_usage": api_usage.to_dict(orient="records"),
        "provider_logs": logs,
    }
    write_json(manifest, latest_dir / "manifest.json")
    write_json(manifest, settings.paths.state_dir / "latest_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
