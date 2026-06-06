from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import TiingoProvider, TwelveDataProvider
from qqq_tracker.settings import Settings

from .daily_run import (
    API_USAGE_COLUMNS,
    FULL_HISTORY_DAYS,
    MIN_HISTORY_ROWS,
    api_usage_row,
    cache_path_for_symbol,
    load_previous_csv,
    load_tiingo_cache,
    merge_price_history,
    normalize_symbol,
    provider_config,
    rel_path,
    result_rate_limit_meta,
    valid_history_row_count,
    write_tiingo_cache,
)

CACHE_QUALITY_COLUMNS = [
    "run_date",
    "symbol",
    "weight",
    "provider",
    "before_rows",
    "after_rows",
    "fetched_rows",
    "is_complete",
    "was_requested",
    "ok",
    "rate_limited",
    "stopped_after_429",
    "retry_after_seconds",
    "message",
]


def as_of_date(value: str) -> str:
    if value == "auto":
        return date.today().isoformat()
    return value


def load_backfill_holdings(settings: Settings, run_date: str) -> pd.DataFrame:
    dated = settings.paths.processed_dir / run_date / "qqq_equity_holdings.csv"
    if dated.exists():
        return pd.read_csv(dated)
    previous = load_previous_csv(settings, "qqq_equity_holdings.csv", [])
    if not previous.empty:
        return previous
    return pd.DataFrame()


def prepare_backfill_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty or "symbol" not in holdings.columns:
        return pd.DataFrame(columns=["symbol", "weight"])
    prepared = holdings.copy()
    prepared["symbol"] = prepared["symbol"].map(normalize_symbol)
    prepared["weight"] = pd.to_numeric(prepared.get("weight", 0.0), errors="coerce").fillna(0.0)
    prepared = prepared[prepared["symbol"].notna() & prepared["symbol"].ne("")]
    prepared = prepared.drop_duplicates(subset=["symbol"], keep="first")
    return prepared.sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)


def cache_quality_row(
    run_date: str,
    symbol: str,
    weight: float,
    before_rows: int,
    after_rows: int,
    provider: str = "tiingo",
    fetched_rows: int = 0,
    was_requested: bool = False,
    ok: bool = False,
    rate_limited: bool = False,
    stopped_after_429: bool = False,
    retry_after_seconds: float | None = None,
    message: str = "",
) -> Dict:
    return {
        "run_date": run_date,
        "symbol": normalize_symbol(symbol),
        "weight": weight,
        "provider": provider,
        "before_rows": int(before_rows),
        "after_rows": int(after_rows),
        "fetched_rows": int(fetched_rows),
        "is_complete": int(after_rows) >= MIN_HISTORY_ROWS,
        "was_requested": bool(was_requested),
        "ok": bool(ok),
        "rate_limited": bool(rate_limited),
        "stopped_after_429": bool(stopped_after_429),
        "retry_after_seconds": retry_after_seconds,
        "message": message,
    }


def backfill_price_cache(
    settings: Settings,
    tiingo: TiingoProvider,
    run_date: str,
    max_calls: int | None = None,
    twelve_data: TwelveDataProvider | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    holdings = prepare_backfill_holdings(load_backfill_holdings(settings, run_date))
    max_calls_per_run = int(max_calls if max_calls is not None else settings.api_limits.get("tiingo", {}).get("max_calls_per_run", 40))
    raw_dir = settings.paths.raw_dir / run_date

    quality_rows: list[Dict] = []
    attempted_symbols: list[str] = []
    loaded_symbols: list[str] = []
    calls_attempted = 0
    calls_success = 0
    rate_limited = False
    stopped_after_429 = False
    retry_after_seconds = None
    messages: list[str] = []
    fallback_candidates: list[dict] = []

    if holdings.empty:
        api_usage = pd.DataFrame(
            [
                api_usage_row(
                    settings,
                    run_date,
                    "tiingo",
                    "daily_prices_cache_backfill",
                    0,
                    0,
                    message="qqq_equity_holdings.csv not found or empty",
                )
            ],
            columns=API_USAGE_COLUMNS,
        )
        manifest = {"ok": False, "run_date": run_date, "message": "qqq_equity_holdings.csv not found or empty"}
        return pd.DataFrame(columns=CACHE_QUALITY_COLUMNS), api_usage, manifest

    for _, holding in holdings.iterrows():
        symbol = holding["symbol"]
        weight = float(holding.get("weight", 0.0))
        cache_df = load_tiingo_cache(settings, symbol)
        before_rows = valid_history_row_count(cache_df)

        if before_rows >= MIN_HISTORY_ROWS:
            quality_rows.append(cache_quality_row(run_date, symbol, weight, before_rows, before_rows, ok=True, message="cache complete"))
            continue

        if stopped_after_429:
            row = cache_quality_row(
                run_date,
                symbol,
                weight,
                before_rows,
                before_rows,
                ok=False,
                rate_limited=True,
                stopped_after_429=True,
                retry_after_seconds=retry_after_seconds,
                message="skipped after Tiingo 429",
            )
            fallback_candidates.append({"row": row, "symbol": symbol, "weight": weight, "before_rows": before_rows, "reason": "tiingo_429_skip"})
            quality_rows.append(row)
            continue

        if calls_attempted >= max_calls_per_run:
            quality_rows.append(cache_quality_row(run_date, symbol, weight, before_rows, before_rows, ok=False, message="skipped after max_calls limit"))
            continue

        attempted_symbols.append(symbol)
        calls_attempted += 1
        end = run_date
        start = (datetime.fromisoformat(end) - timedelta(days=FULL_HISTORY_DAYS)).date().isoformat()
        result = tiingo.daily_prices(symbol, start_date=start, end_date=end)
        result_rate_limited, retry_after = result_rate_limit_meta(result)
        fetched_rows = len(result.data) if result.ok else 0
        live_df = pd.DataFrame()
        if result.ok:
            calls_success += 1
            live_df = result.data
            write_csv(live_df, raw_dir / f"tiingo_{symbol}_cache_backfill.csv")
            messages.append(f"{symbol}: {fetched_rows} rows")
        else:
            messages.append(f"{symbol}: {result.message}")

        merged = merge_price_history(cache_df, live_df, symbol)
        after_rows = valid_history_row_count(merged)
        if after_rows > 0:
            write_tiingo_cache(settings, symbol, merged)
        if result.ok and after_rows >= MIN_HISTORY_ROWS:
            loaded_symbols.append(symbol)

        if result_rate_limited:
            rate_limited = True
            stopped_after_429 = True
            retry_after_seconds = retry_after

        row = cache_quality_row(
            run_date,
            symbol,
            weight,
            before_rows,
            after_rows,
            fetched_rows=fetched_rows,
            was_requested=True,
            ok=result.ok and after_rows >= MIN_HISTORY_ROWS,
            rate_limited=result_rate_limited,
            stopped_after_429=result_rate_limited,
            retry_after_seconds=retry_after,
            message=result.message,
        )
        if (not result.ok or fetched_rows == 0 or after_rows < MIN_HISTORY_ROWS) and calls_attempted <= max_calls_per_run:
            fallback_candidates.append({"row": row, "symbol": symbol, "weight": weight, "before_rows": before_rows, "reason": "tiingo_failed_or_incomplete"})
        quality_rows.append(row)

    twelve_usage = run_twelve_data_fallback(settings, twelve_data, run_date, raw_dir, fallback_candidates)

    cache_quality = pd.DataFrame(quality_rows, columns=CACHE_QUALITY_COLUMNS)
    for candidate in fallback_candidates:
        row = candidate["row"]
        if row.get("provider") not in {"twelve_data", "tiingo+twelve_data"}:
            continue
        mask = cache_quality["symbol"].eq(row["symbol"])
        for col in CACHE_QUALITY_COLUMNS:
            cache_quality.loc[mask, col] = row[col]
    api_usage = pd.DataFrame(
        [
            api_usage_row(
                settings,
                run_date,
                "tiingo",
                "daily_prices_cache_backfill",
                calls_attempted,
                calls_success,
                symbols_requested=attempted_symbols,
                symbols_loaded=loaded_symbols,
                rate_limited=rate_limited,
                stopped_after_429=stopped_after_429,
                retry_after_seconds=retry_after_seconds,
                message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
            )
        ],
        columns=API_USAGE_COLUMNS,
    )
    api_usage = pd.concat([api_usage, pd.DataFrame([twelve_usage], columns=API_USAGE_COLUMNS)], ignore_index=True)
    manifest = {
        "ok": True,
        "run_date": run_date,
        "symbols_total": int(len(holdings)),
        "symbols_complete": int(cache_quality["is_complete"].sum()) if not cache_quality.empty else 0,
        "calls_attempted": calls_attempted,
        "calls_success": calls_success,
        "rate_limited": rate_limited,
        "stopped_after_429": stopped_after_429,
        "retry_after_seconds": retry_after_seconds,
        "fallback_provider": "twelve_data",
        "twelve_data_calls_attempted": int(twelve_usage["calls_attempted"]),
        "twelve_data_symbols_loaded": twelve_usage["symbols_loaded"],
    }
    return cache_quality, api_usage, manifest


def run_twelve_data_fallback(
    settings: Settings,
    twelve_data: TwelveDataProvider | None,
    run_date: str,
    raw_dir: Path,
    candidates: list[dict],
) -> Dict:
    cfg = settings.api_limits.get("twelve_data", {})
    max_credits = int(cfg.get("max_credits_per_run", 160))
    batch_size = int(cfg.get("batch_size", cfg.get("max_credits_per_minute", 8)))
    sleep_seconds = float(cfg.get("sleep_seconds_between_batches", 70))
    outputsize = int(cfg.get("outputsize", 260))
    attempted: list[str] = []
    loaded: list[str] = []
    messages: list[str] = []
    calls_success = 0
    rate_limited = False
    retry_after_seconds = None

    if not candidates:
        return api_usage_row(settings, run_date, "twelve_data", "time_series_cache_fallback", 0, 0, message="no fallback candidates")
    if twelve_data is None or not getattr(twelve_data, "available", False):
        return api_usage_row(
            settings,
            run_date,
            "twelve_data",
            "time_series_cache_fallback",
            0,
            0,
            symbols_requested=[candidate["symbol"] for candidate in candidates],
            message="missing Twelve Data API key; fallback not called",
        )

    for candidate in candidates:
        if len(attempted) >= max_credits or rate_limited:
            break
        if attempted and batch_size > 0 and len(attempted) % batch_size == 0:
            time.sleep(sleep_seconds)
        symbol = candidate["symbol"]
        attempted.append(symbol)
        result = twelve_data.time_series(symbol, outputsize=outputsize, interval="1day")
        result_rate_limited, retry_after = result_rate_limit_meta(result)
        if result.ok and not result.data.empty:
            write_csv(result.data, raw_dir / f"twelve_data_{symbol}_cache_fallback.csv")
            cache_df = load_tiingo_cache(settings, symbol)
            merged = merge_price_history(cache_df, result.data, symbol)
            after_rows = valid_history_row_count(merged)
            if after_rows > 0:
                write_tiingo_cache(settings, symbol, merged)
            if after_rows >= MIN_HISTORY_ROWS:
                calls_success += 1
                loaded.append(symbol)
                row = candidate["row"]
                row["provider"] = "tiingo+twelve_data" if row.get("was_requested") else "twelve_data"
                row["after_rows"] = after_rows
                row["fetched_rows"] = int(row.get("fetched_rows") or 0) + len(result.data)
                row["is_complete"] = True
                row["was_requested"] = True
                row["ok"] = True
                row["message"] = f"{row.get('message')}; twelve_data fallback: {len(result.data)} rows"
            messages.append(f"{symbol}: {len(result.data)} rows")
        else:
            messages.append(f"{symbol}: {result.message}")
        if result_rate_limited:
            rate_limited = True
            retry_after_seconds = retry_after
            row = candidate["row"]
            row["provider"] = "twelve_data" if not row.get("was_requested") else "tiingo+twelve_data"
            row["rate_limited"] = True
            row["stopped_after_429"] = True
            row["retry_after_seconds"] = retry_after
            row["message"] = f"{row.get('message')}; twelve_data rate limited: {result.message}"

    return api_usage_row(
        settings,
        run_date,
        "twelve_data",
        "time_series_cache_fallback",
        len(attempted),
        calls_success,
        symbols_requested=attempted,
        symbols_loaded=loaded,
        rate_limited=rate_limited,
        stopped_after_429=rate_limited,
        retry_after_seconds=retry_after_seconds,
        credits_used=len(attempted),
        message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
    )


def run_backfill(as_of: str = "auto", max_calls: int | None = None) -> dict:
    settings = Settings()
    settings.ensure_dirs()
    run_date = as_of_date(as_of)
    processed_dir = settings.paths.processed_dir / run_date
    latest_dir = settings.paths.reports_latest_dir
    processed_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    timeout = settings.pipeline.get("api", {}).get("request_timeout_seconds", 30)
    retry_count = settings.pipeline.get("api", {}).get("retry_count", 2)
    tiingo_cfg = provider_config(settings, "tiingo")
    tiingo = TiingoProvider(
        settings.get_secret(tiingo_cfg.get("api_key_env", "TIINGO_API_TOKEN")),
        tiingo_cfg.get("base_url", "https://api.tiingo.com/tiingo"),
        timeout=timeout,
        retry_count=retry_count,
    )
    twelve_cfg = provider_config(settings, "twelve_data")
    twelve_data = TwelveDataProvider(
        settings.get_secret(twelve_cfg.get("api_key_env", "TWELVE_DATA_API_KEY")),
        twelve_cfg.get("base_url", "https://api.twelvedata.com"),
        timeout=timeout,
        retry_count=retry_count,
    )

    cache_quality, api_usage, manifest = backfill_price_cache(settings, tiingo, run_date, max_calls=max_calls, twelve_data=twelve_data)
    write_csv(cache_quality, processed_dir / "cache_quality.csv")
    write_csv(api_usage, processed_dir / "price_cache_api_usage.csv")
    write_csv(cache_quality, latest_dir / "cache_quality.csv")
    write_csv(api_usage, latest_dir / "price_cache_api_usage.csv")

    manifest["latest_files"] = {
        "cache_quality_csv": rel_path(latest_dir / "cache_quality.csv", settings.paths.root),
        "price_cache_api_usage_csv": rel_path(latest_dir / "price_cache_api_usage.csv", settings.paths.root),
        "state_manifest_json": rel_path(settings.paths.state_dir / "latest_cache_backfill_manifest.json", settings.paths.root),
    }
    manifest["cache_quality_summary"] = cache_quality.to_dict(orient="records")
    manifest["api_usage"] = api_usage.to_dict(orient="records")
    write_json(manifest, settings.paths.state_dir / "latest_cache_backfill_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
