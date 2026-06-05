from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import AlphaVantageProvider, FMPProvider, FREDProvider, InvescoProvider, TiingoProvider
from qqq_tracker.settings import Settings

from .calculations import (
    annualized_vol,
    drawdown_metrics,
    latest_value,
    moving_average,
    pct_change_from_close,
)
from .report_builder import build_model_input_metrics, build_model_input_metrics_v2, write_excel

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
QQQ_HOLDINGS_COLUMNS = ["date", "symbol", "company_name", "weight", "sector", "source"]
BREADTH_METRICS_COLUMNS = ["metric_name", "metric_value", "denominator", "data_date", "source", "is_missing"]
DATA_QUALITY_COLUMNS = ["dataset", "provider", "ok", "rows", "coverage_ratio", "missing_symbols", "message"]


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
        "invesco": InvescoProvider(
            invesco_cfg.get("holdings_urls", []),
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
    out["source"] = source
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


def quality_row(dataset: str, provider: str, ok: bool, rows: int, coverage_ratio: float | None, missing_symbols: list[str] | None, message: str) -> Dict:
    return {
        "dataset": dataset,
        "provider": provider,
        "ok": bool(ok),
        "rows": rows,
        "coverage_ratio": coverage_ratio,
        "missing_symbols": ",".join(missing_symbols or []),
        "message": message,
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

    rows.extend([
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
    ])

    for series_id, label in [("CPIAUCSL", "CPI近3次变化"), ("PCEPI", "PCE近3次变化")]:
        _, latest_date = fred_latest(fred_frames.get(series_id, pd.DataFrame()))
        changes = fred_recent_pct_changes(fred_frames.get(series_id, pd.DataFrame()), 3)
        for idx, change in enumerate(changes, start=max(1, len(changes) - 2)):
            rows.append({
                "metric_name": f"{series_id}_RECENT_PCT_CHANGE_{idx}",
                "metric_value": change,
                "unit_or_method": f"{label}; pct_change between consecutive observations",
                "data_date": latest_date,
                "source": "FRED",
            })
        rows.append({
            "metric_name": f"{series_id}_LATEST_PCT_CHANGE",
            "metric_value": changes[-1] if changes else None,
            "unit_or_method": f"{label}; latest pct_change between consecutive observations",
            "data_date": latest_date,
            "source": "FRED",
        })

    unrate_value, unrate_date = fred_latest(fred_frames.get("UNRATE", pd.DataFrame()))
    unrate_prior = fred_prior_value(fred_frames.get("UNRATE", pd.DataFrame()), 3)
    unrate_change = unrate_value - unrate_prior if unrate_value is not None and unrate_prior is not None else None
    rows.append({
        "metric_name": "UNRATE_3_OBSERVATION_CHANGE",
        "metric_value": unrate_change,
        "unit_or_method": "current unemployment rate minus value 3 observations ago",
        "data_date": unrate_date,
        "source": "FRED",
    })
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
        quality_rows.append(quality_row("qqq_holdings", result.name, True, len(holdings), 1.0, [], result.message))
        return holdings

    previous = load_previous_csv(settings, "qqq_holdings.csv", QQQ_HOLDINGS_COLUMNS)
    if not previous.empty:
        quality_rows.append(quality_row("qqq_holdings", result.name, False, len(previous), 1.0, [], f"fallback to previous file; {result.message}"))
        logs.append({"provider": result.name, "method": "qqq_holdings_fallback", "symbol": "QQQ", "ok": True, "message": "fallback to previous qqq_holdings.csv"})
        return previous.reindex(columns=QQQ_HOLDINGS_COLUMNS)

    quality_rows.append(quality_row("qqq_holdings", result.name, False, 0, 0.0, [], result.message))
    return pd.DataFrame(columns=QQQ_HOLDINGS_COLUMNS)


def build_breadth_metrics(price_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
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
        latest = close.iloc[-1]
        previous = close.iloc[-2]
        latest_dates.append(str(d.iloc[-1]["date"]))
        records.append({
            "symbol": symbol,
            "up": latest > previous,
            "down": latest < previous,
            "above_ma20": latest > close.tail(20).mean() if len(close) >= 20 else None,
            "above_ma50": latest > close.tail(50).mean() if len(close) >= 50 else None,
            "above_ma200": latest > close.tail(200).mean() if len(close) >= 200 else None,
            "new_high_20d": latest >= close.tail(20).max() if len(close) >= 20 else None,
            "new_low_20d": latest <= close.tail(20).min() if len(close) >= 20 else None,
        })

    data_date = max(latest_dates) if latest_dates else None
    count = len(records)

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
        if metric.endswith("_ratio"):
            value = true_count / denominator if denominator else None
        else:
            value = true_count
        rows.append([metric, value, denominator, data_date, "tiingo", pd.isna(value)])
    advancing, denominator = count_true("up")
    rows.insert(2, ["advancing_ratio", advancing / denominator if denominator else None, denominator, data_date, "tiingo", denominator == 0])
    return pd.DataFrame(rows, columns=BREADTH_METRICS_COLUMNS)


def fetch_breadth(
    settings: Settings,
    providers: Dict[str, object],
    holdings: pd.DataFrame,
    run_date: str,
    raw_dir: Path,
    logs: list[Dict],
    quality_rows: list[Dict],
) -> pd.DataFrame:
    if holdings.empty or "symbol" not in holdings.columns:
        quality_rows.append(quality_row("breadth_metrics", "tiingo", False, 0, 0.0, [], "no holdings symbols available"))
        return pd.DataFrame(columns=BREADTH_METRICS_COLUMNS)

    tiingo: TiingoProvider = providers["tiingo"]  # type: ignore[assignment]
    end = run_date
    start = (datetime.fromisoformat(end) - timedelta(days=420)).date().isoformat()
    symbols = [s for s in holdings["symbol"].dropna().astype(str).str.replace(".", "-", regex=False).unique().tolist() if s]
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for symbol in symbols:
        result = tiingo.daily_prices(symbol, start_date=start, end_date=end)
        logs.append({"provider": result.name, "method": "breadth_daily_prices", "symbol": symbol, "ok": result.ok, "message": result.message})
        if result.ok:
            frames[symbol] = result.data
            write_csv(result.data, raw_dir / f"tiingo_{symbol}_breadth_daily.csv")
        else:
            missing.append(symbol)
    metrics = build_breadth_metrics(frames)
    requested = len(symbols)
    ok_count = len(frames)
    quality_rows.append(quality_row(
        "breadth_metrics",
        "tiingo",
        ok_count > 0,
        len(metrics),
        ok_count / requested if requested else 0.0,
        missing,
        f"{ok_count}/{requested} symbols fetched",
    ))
    return metrics


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
    price_source_frames: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    price_daily_frames: list[pd.DataFrame] = []

    # Alpha Vantage price source
    if settings.pipeline.get("run", {}).get("fetch_alpha_vantage_prices", True):
        av: AlphaVantageProvider = providers["alpha_vantage"]  # type: ignore[assignment]
        for symbol in settings.symbols.get("price_symbols", ["QQQ"]):
            result = av.daily_adjusted(symbol, outputsize=provider_config(settings, "alpha_vantage").get("default_outputsize", "compact"))
            logs.append({"provider": result.name, "method": "daily_adjusted", "symbol": symbol, "ok": result.ok, "message": result.message})
            if result.ok:
                df = result.data
                write_csv(df, raw_dir / f"alpha_vantage_{symbol}_daily.csv")
                price_source_frames.setdefault(symbol, []).append(("alpha_vantage", df))
                price_daily_frames.append(normalize_price_daily(df, symbol, "alpha_vantage"))
            quality_rows.append(quality_row("price_daily", result.name, result.ok, len(result.data), 1.0 if result.ok else 0.0, [] if result.ok else [symbol], result.message))

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
                price_source_frames.setdefault(symbol, []).append(("tiingo", df))
                price_daily_frames.append(normalize_price_daily(df, symbol, "tiingo"))
            quality_rows.append(quality_row("price_daily", result.name, result.ok, len(result.data), 1.0 if result.ok else 0.0, [] if result.ok else [symbol], result.message))

    price_daily = pd.concat(price_daily_frames, ignore_index=True) if price_daily_frames else pd.DataFrame(columns=PRICE_DAILY_COLUMNS)
    price_daily = price_daily.reindex(columns=PRICE_DAILY_COLUMNS).sort_values(["symbol", "source", "date"]) if not price_daily.empty else price_daily
    price_metrics_rows = []
    for symbol, frames in price_source_frames.items():
        source, frame = choose_metric_frame(frames)
        price_metrics_rows.append(summarize_price(symbol, frame, source))
    price_metrics = pd.DataFrame(price_metrics_rows, columns=PRICE_METRICS_COLUMNS)
    write_csv(price_daily, processed_dir / "price_daily.csv")
    write_csv(price_metrics, processed_dir / "price_metrics.csv")

    # FRED macro
    macro_daily_rows = []
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
                macro_daily_rows.append({
                    "series_id": series_id,
                    "name": name,
                    "latest_date": last_date,
                    "latest_value": latest,
                    "source": "FRED",
                })
            quality_rows.append(quality_row("macro_daily", result.name, result.ok, len(result.data), 1.0 if result.ok else 0.0, [] if result.ok else [series_id], result.message))
    macro_daily = pd.DataFrame(macro_daily_rows, columns=MACRO_DAILY_COLUMNS)
    macro_metrics = pd.DataFrame(build_macro_metric_rows(fred_frames), columns=MACRO_METRICS_COLUMNS)
    write_csv(macro_daily, processed_dir / "macro_daily.csv")
    write_csv(macro_metrics, processed_dir / "macro_metrics.csv")

    # QQQ holdings and breadth
    qqq_holdings = pd.DataFrame(columns=QQQ_HOLDINGS_COLUMNS)
    if settings.pipeline.get("run", {}).get("fetch_qqq_holdings", True):
        qqq_holdings = fetch_holdings(settings, providers, logs, quality_rows)
    qqq_holdings = qqq_holdings.reindex(columns=QQQ_HOLDINGS_COLUMNS)
    write_csv(qqq_holdings, processed_dir / "qqq_holdings.csv")

    breadth_metrics = pd.DataFrame(columns=BREADTH_METRICS_COLUMNS)
    if settings.pipeline.get("run", {}).get("fetch_breadth_metrics", True):
        breadth_metrics = fetch_breadth(settings, providers, qqq_holdings, run_date, raw_dir, logs, quality_rows)
    breadth_metrics = breadth_metrics.reindex(columns=BREADTH_METRICS_COLUMNS)
    write_csv(breadth_metrics, processed_dir / "breadth_metrics.csv")

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
        requested = len(settings.symbols.get("fundamental_symbols", []))
        missing = [r["symbol"] for r in fmp_rows if not r["ok"]]
        quality_rows.append(quality_row(
            "fmp_summary",
            "fmp",
            any(r["ok"] for r in fmp_rows),
            len(fmp_rows),
            (requested - len(missing)) / requested if requested else 0.0,
            missing,
            f"{requested - len(missing)}/{requested} quotes fetched",
        ))

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
    data_quality = pd.DataFrame(quality_rows, columns=DATA_QUALITY_COLUMNS)
    write_csv(logs_df, processed_dir / "run_log.csv")
    write_csv(data_quality, processed_dir / "data_quality.csv")

    model_input_metrics = build_model_input_metrics_v2(price_metrics, macro_daily, macro_metrics, fmp_summary, breadth_metrics, data_quality)
    write_csv(model_input_metrics, processed_dir / "model_input_metrics.csv")
    write_csv(model_input_metrics, latest_dir / "model_input_metrics.csv")
    write_csv(price_daily, latest_dir / "price_daily.csv")
    write_csv(price_metrics, latest_dir / "price_metrics.csv")
    write_csv(macro_daily, latest_dir / "macro_daily.csv")
    write_csv(macro_metrics, latest_dir / "macro_metrics.csv")
    write_csv(qqq_holdings, latest_dir / "qqq_holdings.csv")
    write_csv(breadth_metrics, latest_dir / "breadth_metrics.csv")
    write_csv(data_quality, latest_dir / "data_quality.csv")
    write_csv(fmp_summary, latest_dir / "fmp_summary.csv")
    write_csv(logs_df, latest_dir / "run_log.csv")

    xlsx_path = latest_dir / "nasdaq100_qqq_daily_tracker.xlsx"
    write_excel(xlsx_path, {
        "价格日线": price_daily,
        "价格指标": price_metrics,
        "宏观原始": macro_daily,
        "宏观指标": macro_metrics,
        "QQQ持仓": qqq_holdings,
        "市场广度": breadth_metrics,
        "数据质量": data_quality,
        "FMP可用性": fmp_summary,
        "FMP关键指标": key_metrics,
        "模型输入指标": model_input_metrics,
        "运行日志": logs_df,
    })

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
            "breadth_metrics_csv": rel_path(latest_dir / "breadth_metrics.csv", settings.paths.root),
            "data_quality_csv": rel_path(latest_dir / "data_quality.csv", settings.paths.root),
            "fmp_summary_csv": rel_path(latest_dir / "fmp_summary.csv", settings.paths.root),
            "run_log_csv": rel_path(latest_dir / "run_log.csv", settings.paths.root),
        },
        "quality_summary": data_quality.to_dict(orient="records"),
        "provider_logs": logs,
    }
    write_json(manifest, latest_dir / "manifest.json")
    write_json(manifest, settings.paths.state_dir / "latest_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
